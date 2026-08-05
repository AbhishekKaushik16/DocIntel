"""
Pipeline Orchestrator

Coordinates the 4-stage processing pipeline for a single document:
  1. Parse → 2. Classify → 3. Extract → 4. Validate

Concurrency guarantees
──────────────────────
• RC-1  Atomic ownership claim: uses a single UPDATE … WHERE status IN (…) RETURNING *
        so that only one Celery worker can transition a document from a claimable state
        to PROCESSING. Any concurrent worker gets 0 rows and exits immediately.
        We deliberately use ONE session for both the claim and the pipeline work to
        avoid asyncpg's "Future attached to a different loop" error in Celery prefork
        workers (asyncpg connections are pinned to the event loop they were created on).

• RC-2  Idempotent field writes: before inserting extracted_fields rows, all
        pre-existing rows for the document are deleted in the same transaction.
        This ensures retries and reprocesses never accumulate duplicate rows.
"""

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models import (
    Document,
    DocumentStatus,
    ExtractedField,
    PipelineStage,
    ProcessingLog,
    StageStatus,
)
from app.pipeline.classifier import classify_document
from app.pipeline.parser import parse_document
from app.pipeline.extractor import extract_structured_data
from app.pipeline.validator import validate_and_score


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _log_stage(
    db: AsyncSession,
    document_id: uuid.UUID,
    stage: PipelineStage,
    status: StageStatus,
    duration_ms: int | None = None,
    metadata: dict | None = None,
    error_message: str | None = None,
) -> None:
    """Write a processing log entry (flushed but not committed — caller commits)."""
    db.add(ProcessingLog(
        document_id=document_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        metadata_=metadata or {},
        error_message=error_message,
    ))
    await db.flush()


# ── Main entry point ───────────────────────────────────────────────────────────

async def process_document(document_id: str) -> None:
    """
    Run the full extraction pipeline on a document.

    Called by the Celery task worker. Opens its own DB session and manages
    the full document lifecycle.

    Concurrency (RC-1): uses a single atomic UPDATE … WHERE status IN (…) RETURNING *
    to claim the document. Only one worker transitions it to PROCESSING; any other
    concurrent worker gets zero rows back and exits immediately.

    A single session is used throughout (claim + pipeline) to avoid asyncpg's
    'Future attached to a different loop' RuntimeError in Celery prefork workers.
    """
    doc_uuid = uuid.UUID(document_id)

    async with async_session_factory() as db:
        try:
            # ── RC-1: Atomic ownership claim ──────────────────────────────────
            # Single UPDATE … RETURNING is fully atomic in Postgres (no TOCTOU).
            # If another worker already claimed the row we get 0 rows → return.
            claim_result = await db.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .where(Document.status.in_([
                    DocumentStatus.PENDING,
                    DocumentStatus.FAILED,
                    DocumentStatus.NEEDS_REVIEW,
                ]))
                .values(status=DocumentStatus.PROCESSING)
                .returning(Document)
            )
            await db.commit()

            document = claim_result.scalar_one_or_none()
            if document is None:
                # Document doesn't exist, is COMPLETED, or another worker
                # already claimed it — nothing to do.
                return

            # ── Stage 1: Parse ────────────────────────────────────────────────
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.PARSE, StageStatus.STARTED)

            try:
                parse_result = await parse_document(document.file_path)
                duration = int((time.monotonic() - t0) * 1000)

                if not parse_result.text.strip():
                    await _log_stage(
                        db, doc_uuid, PipelineStage.PARSE, StageStatus.FAILED,
                        duration_ms=duration,
                        error_message="No text could be extracted from the document.",
                        metadata={"warnings": parse_result.warnings},
                    )
                    document.status = DocumentStatus.FAILED
                    await db.commit()
                    return

                document.raw_text = parse_result.text
                await _log_stage(
                    db, doc_uuid, PipelineStage.PARSE, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={
                        "method": parse_result.method,
                        "page_count": parse_result.page_count,
                        "warnings": parse_result.warnings,
                        "parser_metadata": parse_result.metadata,
                    },
                )
                await db.commit()

            except Exception as e:
                duration = int((time.monotonic() - t0) * 1000)
                await _log_stage(
                    db, doc_uuid, PipelineStage.PARSE, StageStatus.FAILED,
                    duration_ms=duration,
                    error_message=str(e),
                )
                document.status = DocumentStatus.FAILED
                await db.commit()
                return

            # ── Stage 2: Classify ─────────────────────────────────────────────
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.STARTED)

            try:
                classification = await classify_document(parse_result.text)
                duration = int((time.monotonic() - t0) * 1000)

                document.document_type = classification.document_type
                await _log_stage(
                    db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={
                        "type": classification.document_type,
                        "confidence": classification.confidence,
                        "method": classification.method,
                    },
                )
                await db.commit()

            except Exception as e:
                duration = int((time.monotonic() - t0) * 1000)
                await _log_stage(
                    db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.FAILED,
                    duration_ms=duration,
                    error_message=str(e),
                )
                # Classification failure is non-fatal — fall back to "generic"
                document.document_type = document.document_type or "generic"
                await db.commit()

            # ── Stage 3: Extract ──────────────────────────────────────────────
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.EXTRACT, StageStatus.STARTED)

            try:
                extracted_data, extraction_method = await extract_structured_data(
                    parse_result.text,
                    document.document_type,
                )
                duration = int((time.monotonic() - t0) * 1000)

                document.extracted_data = extracted_data
                await _log_stage(
                    db, doc_uuid, PipelineStage.EXTRACT, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={"method": extraction_method},
                )

                # RC-2: Delete stale extracted_fields before inserting new ones
                # so retries never accumulate duplicate rows.
                await db.execute(
                    delete(ExtractedField).where(ExtractedField.document_id == doc_uuid)
                )
                for field_name, field_value in extracted_data.items():
                    if field_value is not None and not isinstance(field_value, (list, dict)):
                        db.add(ExtractedField(
                            document_id=doc_uuid,
                            field_name=field_name,
                            field_value=str(field_value),
                            field_type=type(field_value).__name__,
                        ))

                await db.commit()

            except Exception as e:
                duration = int((time.monotonic() - t0) * 1000)
                await _log_stage(
                    db, doc_uuid, PipelineStage.EXTRACT, StageStatus.FAILED,
                    duration_ms=duration,
                    error_message=str(e),
                )
                document.status = DocumentStatus.FAILED
                await db.commit()
                return

            # ── Stage 4: Validate ─────────────────────────────────────────────
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.VALIDATE, StageStatus.STARTED)

            try:
                validation = validate_and_score(
                    extracted_data=extracted_data,
                    document_type=document.document_type,
                    extraction_method=extraction_method,
                    parse_warnings=parse_result.warnings,
                )
                duration = int((time.monotonic() - t0) * 1000)

                document.confidence_score = validation.confidence_score

                if validation.confidence_score >= settings.confidence_auto_approve:
                    document.status = DocumentStatus.COMPLETED
                elif validation.confidence_score >= settings.confidence_review_threshold:
                    document.status = DocumentStatus.NEEDS_REVIEW
                else:
                    document.status = DocumentStatus.FAILED

                document.processed_at = datetime.now(timezone.utc)

                await _log_stage(
                    db, doc_uuid, PipelineStage.VALIDATE, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={
                        "confidence_score": validation.confidence_score,
                        "field_completeness": validation.field_completeness,
                        "cross_validation_score": validation.cross_validation_score,
                        "issues": [
                            {"field": i.field, "severity": i.severity, "message": i.message}
                            for i in validation.issues
                        ],
                    },
                )
                await db.commit()

            except Exception as e:
                duration = int((time.monotonic() - t0) * 1000)
                await _log_stage(
                    db, doc_uuid, PipelineStage.VALIDATE, StageStatus.FAILED,
                    duration_ms=duration,
                    error_message=str(e),
                )
                document.status = DocumentStatus.NEEDS_REVIEW
                document.processed_at = datetime.now(timezone.utc)
                await db.commit()

            # ── Update full-text search vector ────────────────────────────────
            # Non-fatal: failure here does not affect document status.
            try:
                search_parts = [document.raw_text or ""]
                if document.extracted_data:
                    for v in document.extracted_data.values():
                        if isinstance(v, str):
                            search_parts.append(v)
                        elif isinstance(v, list):
                            for item in v:
                                if isinstance(item, str):
                                    search_parts.append(item)
                                elif isinstance(item, dict):
                                    search_parts.extend(
                                        str(val) for val in item.values()
                                        if val and isinstance(val, str)
                                    )

                await db.execute(
                    text(
                        "UPDATE documents SET search_vector = to_tsvector('english', :txt) "
                        "WHERE id = :doc_id"
                    ),
                    {"txt": " ".join(search_parts)[:100_000], "doc_id": str(doc_uuid)},
                )
                await db.commit()
            except Exception:
                pass  # Non-fatal — search index may be stale but data is safe

        except Exception:
            # Catch-all safety net: ensure document never stays PROCESSING forever.
            try:
                await db.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .where(Document.status == DocumentStatus.PROCESSING)
                    .values(
                        status=DocumentStatus.FAILED,
                        processed_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
            except Exception:
                pass
