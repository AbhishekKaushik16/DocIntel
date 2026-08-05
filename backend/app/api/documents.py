"""Document API routes — upload, list, detail, corrections, reprocess, delete."""

import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Document, DocumentStatus, DocumentType, ExtractedField
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

        # Save file to disk
        doc_id = uuid.uuid4()
        safe_filename = f"{doc_id}{ext}"
        file_path = upload_dir / safe_filename

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        # Create database record
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

    await db.flush()
    # Make uploaded document rows visible to the Celery worker before enqueueing.
    await db.commit()

    # Trigger background processing for each uploaded document
    for resp in responses:
        if resp.status == DocumentStatus.PENDING:
            # Import here to avoid circular imports with celery
            from app.pipeline.tasks import process_document_task

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
    type_filter: DocumentType | None = Query(None, alias="type"),
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
    """Apply human corrections to extracted fields."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    for correction in body.corrections:
        # Find or create the extracted field
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
            new_field = ExtractedField(
                document_id=document_id,
                field_name=correction.field_name,
                field_value=correction.field_value,
                human_verified=True,
                confidence=1.0,
            )
            db.add(new_field)

        # Also update the JSONB extracted_data
        if document.extracted_data is None:
            document.extracted_data = {}
        document.extracted_data[correction.field_name] = correction.field_value

    # If document was needs_review, mark as completed after corrections
    if document.status == DocumentStatus.NEEDS_REVIEW:
        document.status = DocumentStatus.COMPLETED

    await db.flush()

    # Re-fetch to get updated relationships
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
    """Re-run the extraction pipeline on a document."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    document.status = DocumentStatus.PENDING
    document.confidence_score = None
    await db.flush()

    from app.pipeline.tasks import process_document_task
    process_document_task.delay(str(document_id))

    return UploadResponse(
        id=document.id,
        original_filename=document.original_filename,
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
    """Delete a document and all its extracted data."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete the file from disk
    file_path = Path(document.file_path)
    if file_path.exists():
        file_path.unlink()

    await db.delete(document)


# ──────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────


@router.get("/stats/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate statistics for the dashboard."""
    # Total documents
    total_result = await db.execute(select(func.count(Document.id)))
    total = total_result.scalar() or 0

    # By status
    status_counts = {}
    for s in DocumentStatus:
        count_result = await db.execute(
            select(func.count(Document.id)).where(Document.status == s)
        )
        status_counts[s.value] = count_result.scalar() or 0

    # By type
    type_query = select(Document.document_type, func.count(Document.id)).group_by(
        Document.document_type
    )
    type_result = await db.execute(type_query)
    by_type = {
        (row[0].value if row[0] else "unclassified"): row[1]
        for row in type_result.all()
    }

    # Average confidence
    avg_result = await db.execute(
        select(func.avg(Document.confidence_score)).where(
            Document.confidence_score.is_not(None)
        )
    )
    avg_confidence = avg_result.scalar()

    return DashboardStats(
        total_documents=total,
        completed=status_counts.get("completed", 0),
        processing=status_counts.get("processing", 0),
        needs_review=status_counts.get("needs_review", 0),
        failed=status_counts.get("failed", 0),
        by_type=by_type,
        avg_confidence=round(avg_confidence, 3) if avg_confidence else None,
    )
