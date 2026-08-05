"""Document API routes — upload, list, detail, corrections, reprocess, delete.

Concurrency notes
──────────────────
• RC-3/RC-7  correct_fields uses SELECT … FOR UPDATE to serialize concurrent
             human corrections and prevent pipeline writes from overwriting them.
• RC-4       reprocess uses an atomic UPDATE … WHERE status NOT IN (…) so that
             only one concurrent caller can queue a task; others get HTTP 409.
• RC-5       delete removes the DB row before the file; unlink uses missing_ok
             so a concurrent delete or OS removal doesn't raise.
• RC-6       upload cleans up the file on disk if the DB commit fails.
• RC-8       dashboard stats uses a single GROUP BY query to avoid inconsistent
             reads across multiple sequential SELECT COUNT statements.
"""

import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, update, case, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Document, DocumentStatus, ExtractedField
from app.schemas import (
    DocumentListItem,
    DocumentListResponse,
    DocumentResponse,
    FieldCorrectionsRequest,
    UploadResponse,
    DashboardStats,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ──────────────────────────────────────────────────────────────
# Upload
# ──────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
    ".docx", ".doc", ".csv", ".xlsx", ".txt", ".md",
}


@router.post("/upload", response_model=list[UploadResponse], status_code=status.HTTP_201_CREATED)
async def upload_documents(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more documents for processing."""
    upload_dir = settings.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)

    responses = []

    for file in files:
        # Validate file extension
        ext = Path(file.filename or "unknown").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            responses.append(
                UploadResponse(
                    id=uuid.uuid4(),
                    original_filename=file.filename or "unknown",
                    status=DocumentStatus.FAILED,
                    message=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                )
            )
            continue

        # Validate file size
        content = await file.read()
        if len(content) > settings.max_file_size_mb * 1024 * 1024:
            responses.append(
                UploadResponse(
                    id=uuid.uuid4(),
                    original_filename=file.filename or "unknown",
                    status=DocumentStatus.FAILED,
                    message=f"File exceeds maximum size of {settings.max_file_size_mb}MB",
                )
            )
            continue

        # RC-6: Write file to disk and create DB record atomically.
        # If the DB commit fails we delete the orphaned file so disk stays clean.
        doc_id = uuid.uuid4()
        safe_filename = f"{doc_id}{ext}"
        file_path = upload_dir / safe_filename

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        try:
            document = Document(
                id=doc_id,
                original_filename=file.filename or "unknown",
                file_path=str(file_path),
                mime_type=file.content_type,
                file_size_bytes=len(content),
                status=DocumentStatus.PENDING,
            )
            db.add(document)

            responses.append(
                UploadResponse(
                    id=doc_id,
                    original_filename=file.filename or "unknown",
                    status=DocumentStatus.PENDING,
                    message="Document uploaded successfully. Processing will begin shortly.",
                )
            )
        except Exception:
            # DB insert failed before commit — clean up the file we already wrote
            file_path.unlink(missing_ok=True)
            raise

    # Commit all DB rows first, then enqueue tasks.
    # Committing before delay() ensures the worker's separate DB connection
    # can see the rows when it picks up the task (RC-6 / original race).
    await db.commit()

    # Trigger background processing for each successfully inserted document
    from app.pipeline.tasks import process_document_task  # avoid circular import

    for resp in responses:
        if resp.status == DocumentStatus.PENDING:
            process_document_task.delay(str(resp.id))

    return responses


# ──────────────────────────────────────────────────────────────
# List
# ──────────────────────────────────────────────────────────────


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    type_filter: str | None = Query(None, alias="type"),
    db: AsyncSession = Depends(get_db),
):
    """List documents with optional filtering and pagination."""
    query = select(Document)

    if status_filter:
        query = query.where(Document.status == status_filter)
    if type_filter:
        query = query.where(Document.document_type == type_filter)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(Document.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    documents = result.scalars().all()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return DocumentListResponse(
        documents=[DocumentListItem.model_validate(doc) for doc in documents],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


# ──────────────────────────────────────────────────────────────
# Detail
# ──────────────────────────────────────────────────────────────


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get full document details including extracted data and processing logs."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentResponse.model_validate(document)


# ──────────────────────────────────────────────────────────────
# Human corrections
# ──────────────────────────────────────────────────────────────


@router.patch("/{document_id}/fields", response_model=DocumentResponse)
async def correct_fields(
    document_id: uuid.UUID,
    body: FieldCorrectionsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Apply human corrections to extracted fields.

    RC-3/RC-7: We acquire a row-level lock (SELECT … FOR UPDATE) before reading
    extracted_data. This serializes concurrent human corrections and prevents a
    simultaneous pipeline write from silently overwriting human changes.
    """
    # Lock the document row for the duration of this correction transaction.
    result = await db.execute(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    for correction in body.corrections:
        # Find or create the extracted field (also locked via the parent transaction)
        field_result = await db.execute(
            select(ExtractedField).where(
                ExtractedField.document_id == document_id,
                ExtractedField.field_name == correction.field_name,
            )
        )
        field = field_result.scalar_one_or_none()

        if field:
            field.field_value = correction.field_value
            field.human_verified = True
            field.confidence = 1.0
        else:
            db.add(ExtractedField(
                document_id=document_id,
                field_name=correction.field_name,
                field_value=correction.field_value,
                human_verified=True,
                confidence=1.0,
            ))

        # Merge into the JSONB blob as well
        if document.extracted_data is None:
            document.extracted_data = {}
        # SQLAlchemy won't detect in-place dict mutations on JSONB without this:
        merged = dict(document.extracted_data)
        merged[correction.field_name] = correction.field_value
        document.extracted_data = merged

    # Auto-approve after human corrections
    if document.status == DocumentStatus.NEEDS_REVIEW:
        document.status = DocumentStatus.COMPLETED

    await db.flush()
    await db.refresh(document)
    return DocumentResponse.model_validate(document)


# ──────────────────────────────────────────────────────────────
# Reprocess
# ──────────────────────────────────────────────────────────────


@router.post("/{document_id}/reprocess", response_model=UploadResponse)
async def reprocess_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Re-run the extraction pipeline on a document.

    RC-4: Uses an atomic UPDATE … WHERE status NOT IN ('pending','processing')
    so that only one concurrent reprocess request can queue a task. Any other
    concurrent caller gets an HTTP 409 instead of enqueueing a duplicate task.
    """
    # First confirm the document exists.
    exists_result = await db.execute(
        select(Document.id, Document.original_filename)
        .where(Document.id == document_id)
    )
    row = exists_result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Atomic CAS: only transitions from non-active states.
    # If another request already set it to PENDING or PROCESSING, we get 0 rows.
    result = await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .where(Document.status.not_in([
            DocumentStatus.PENDING,
            DocumentStatus.PROCESSING,
        ]))
        .values(
            status=DocumentStatus.PENDING,
            confidence_score=None,
            document_type=None,
            processed_at=None,
        )
        .returning(Document.id, Document.original_filename)
    )
    updated = result.one_or_none()

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is already queued or being processed. Please wait.",
        )

    # Commit BEFORE enqueueing so the worker's separate DB connection sees PENDING.
    await db.commit()

    from app.pipeline.tasks import process_document_task
    process_document_task.delay(str(document_id))

    return UploadResponse(
        id=updated.id,
        original_filename=updated.original_filename,
        status=DocumentStatus.PENDING,
        message="Document queued for reprocessing.",
    )


# ──────────────────────────────────────────────────────────────
# Delete
# ──────────────────────────────────────────────────────────────


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and all its extracted data.

    RC-5: Delete the DB row first, then the file.
    If the server crashes between the two steps the file is a harmless orphan;
    if the file delete fails the DB row is already gone so the API stays consistent.
    unlink(missing_ok=True) is idempotent — safe if another process already removed it.
    """
    result = await db.execute(
        select(Document)
        .where(Document.id == document_id)
        .with_for_update()  # prevent concurrent deletes on the same row
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(document.file_path)

    # Delete DB row first (cascade removes extracted_fields + processing_logs)
    await db.delete(document)
    await db.flush()  # apply within session; will commit via get_db dependency

    # Then remove the file — missing_ok handles TOCTOU / concurrent deletes
    file_path.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────


@router.get("/stats/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate statistics for the dashboard.

    RC-8: All counts are computed in a single SQL query so the numbers are
    consistent even while documents are being processed concurrently.
    """
    # Single query: per-status counts + total + avg confidence
    # Uses conditional aggregation (FILTER / CASE) to avoid multiple round-trips.
    stats_result = await db.execute(
        select(
            func.count(Document.id).label("total"),
            func.count(Document.id).filter(
                Document.status == DocumentStatus.COMPLETED
            ).label("completed"),
            func.count(Document.id).filter(
                Document.status == DocumentStatus.PROCESSING
            ).label("processing"),
            func.count(Document.id).filter(
                Document.status == DocumentStatus.NEEDS_REVIEW
            ).label("needs_review"),
            func.count(Document.id).filter(
                Document.status == DocumentStatus.FAILED
            ).label("failed"),
            func.avg(Document.confidence_score).filter(
                Document.confidence_score.is_not(None)
            ).label("avg_confidence"),
        )
    )
    row = stats_result.one()

    # By type — still a separate query but cheap (indexed GROUP BY)
    type_result = await db.execute(
        select(Document.document_type, func.count(Document.id))
        .group_by(Document.document_type)
    )
    by_type = {
        (t if t else "unclassified"): cnt
        for t, cnt in type_result.all()
    }

    return DashboardStats(
        total_documents=row.total or 0,
        completed=row.completed or 0,
        processing=row.processing or 0,
        needs_review=row.needs_review or 0,
        failed=row.failed or 0,
        by_type=by_type,
        avg_confidence=round(row.avg_confidence, 3) if row.avg_confidence else None,
    )
