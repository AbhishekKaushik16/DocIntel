"""
Pipeline Orchestrator

Coordinates the 4-stage processing pipeline for a single document:
  1. Classify → 2. Parse → 3. Extract → 4. Validate

Each stage is logged to the processing_logs table for full observability.
Handles errors gracefully — a failure in one stage doesn't crash the pipeline.
"""

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
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


async def _log_stage(
    db: AsyncSession,
    document_id: uuid.UUID,
    stage: PipelineStage,
    status: StageStatus,
    duration_ms: int | None = None,
    metadata: dict | None = None,
    error_message: str | None = None,
):
    """Write a processing log entry."""
    log = ProcessingLog(
        document_id=document_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        metadata_=metadata or {},
        error_message=error_message,
    )
    db.add(log)
    await db.flush()


async def process_document(document_id: str) -> None:
    """
    Run the full extraction pipeline on a document.

    This is called by the Celery task worker. It opens its own
    database session and manages the full lifecycle.
    """
    doc_uuid = uuid.UUID(document_id)

    async with async_session_factory() as db:
        try:
            # Fetch the document
            result = await db.execute(select(Document).where(Document.id == doc_uuid))
            document = result.scalar_one_or_none()

            if not document:
                return

            # Mark as processing
            document.status = DocumentStatus.PROCESSING
            await db.commit()

            # ── Stage 1: Classify ─────────────────────────────────
            # We need the raw text first to classify, so we parse first in practice.
            # But we log classification after we have text.

            # ── Stage 2: Parse ────────────────────────────────────
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

            # ── Stage 1 (actual): Classify ────────────────────────
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
                        "type": classification.document_type.value,
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
                # Classification failure isn't fatal — default to GENERIC
                document.document_type = document.document_type or "generic"
                await db.commit()

            # ── Stage 3: Extract ──────────────────────────────────
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

                # Normalize extracted fields into the fields table
                for field_name, field_value in extracted_data.items():
                    if field_value is not None and not isinstance(field_value, (list, dict)):
                        ef = ExtractedField(
                            document_id=doc_uuid,
                            field_name=field_name,
                            field_value=str(field_value),
                            field_type=type(field_value).__name__,
                        )
                        db.add(ef)

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

            # ── Stage 4: Validate ─────────────────────────────────
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

                # Route based on confidence
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

            # ── Update search vector ──────────────────────────────
            # Build search text from raw_text + extracted field values
            try:
                search_parts = [document.raw_text or ""]
                if document.extracted_data:
                    for k, v in document.extracted_data.items():
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

                search_text = " ".join(search_parts)

                # Update search_vector using raw SQL for tsvector
                from sqlalchemy import text
                await db.execute(
                    text(
                        "UPDATE documents SET search_vector = to_tsvector('english', :text) "
                        "WHERE id = :doc_id"
                    ),
                    {"text": search_text[:100000], "doc_id": str(doc_uuid)},
                )
                await db.commit()
            except Exception:
                # Search vector update failure is non-fatal
                pass

        except Exception as e:
            # Catch-all: mark as failed
            try:
                document.status = DocumentStatus.FAILED
                document.processed_at = datetime.now(timezone.utc)
                await db.commit()
            except Exception:
                pass
