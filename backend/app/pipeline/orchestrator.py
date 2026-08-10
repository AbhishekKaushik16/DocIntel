"""
Pipeline Orchestrator — Thin wrapper around the LangGraph pipeline.

Responsibilities:
  1. Atomic ownership claim (RC-1)
  2. Invoke the LangGraph pipeline
  3. Persist final state to the database
  4. Log stage transitions for observability
  5. Index to Elasticsearch (non-fatal)

All pipeline logic (branching, retries, error handling) is now in graph.py.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

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
from app.pipeline.graph import pipeline, PipelineState


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _log_stage(
    db: AsyncSession,
    document_id: uuid.UUID,
    stage: PipelineStage,
    status: StageStatus,
    duration_ms: int | None = None,
    metadata: dict | None = None,
    error_message: str | None = None,
    reasoning: str | None = None,
    agent_steps: list[dict] | None = None,
) -> None:
    """Write a processing log entry."""
    db.add(ProcessingLog(
        document_id=document_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        metadata_=metadata or {},
        error_message=error_message,
        reasoning=reasoning,
        agent_steps=agent_steps,
    ))
    await db.flush()


async def _write_extracted_fields(
    db: AsyncSession,
    doc_uuid: uuid.UUID,
    extracted_data: dict[str, Any],
) -> None:
    """RC-2: Idempotent extracted_fields write — delete stale rows first."""
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


STATUS_MAP = {
    "completed": DocumentStatus.COMPLETED,
    "needs_review": DocumentStatus.NEEDS_REVIEW,
    "failed": DocumentStatus.FAILED,
}

STAGE_MAP = {
    "parse": PipelineStage.PARSE,
    "classify": PipelineStage.CLASSIFY,
    "extract": PipelineStage.EXTRACT,
    "validate": PipelineStage.VALIDATE,
    "resolve": PipelineStage.RESOLVE,
    "re_validate": PipelineStage.VALIDATE,
}


async def _persist_pipeline_logs(
    db: AsyncSession,
    doc_uuid: uuid.UUID,
    state: dict[str, Any],
) -> None:
    """Persist pipeline stage logs from the final LangGraph state."""
    timings = state.get("stage_timings", {})

    # Parse log
    if "raw_text" in state or state.get("error", "").startswith("Parse"):
        await _log_stage(
            db, doc_uuid, PipelineStage.PARSE,
            StageStatus.FAILED if state.get("error", "").startswith("Parse") or state.get("error", "").startswith("No text") else StageStatus.COMPLETED,
            duration_ms=timings.get("parse"),
            metadata={
                "method": state.get("parse_method"),
                "page_count": state.get("page_count"),
                "warnings": state.get("parse_warnings", []),
            },
            error_message=state.get("error") if state.get("error", "").startswith("Parse") or state.get("error", "").startswith("No text") else None,
        )

    # Classify log
    if "document_type" in state:
        await _log_stage(
            db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.COMPLETED,
            duration_ms=timings.get("classify"),
            metadata={
                "type": state.get("document_type"),
                "confidence": state.get("classify_confidence"),
                "method": state.get("classify_method"),
            },
            reasoning=state.get("classify_reasoning"),
            agent_steps=state.get("classify_agent_steps"),
        )

    # Extract log
    if "extracted_data" in state or state.get("error", "").startswith("Extraction"):
        await _log_stage(
            db, doc_uuid, PipelineStage.EXTRACT,
            StageStatus.FAILED if state.get("error", "").startswith("Extraction") else StageStatus.COMPLETED,
            duration_ms=timings.get("extract"),
            metadata={"method": state.get("extraction_method")},
            error_message=state.get("error") if state.get("error", "").startswith("Extraction") else None,
        )

    # Validate log
    if "confidence_score" in state:
        await _log_stage(
            db, doc_uuid, PipelineStage.VALIDATE, StageStatus.COMPLETED,
            duration_ms=timings.get("validate"),
            metadata={
                "confidence_score": state.get("confidence_score"),
                "field_completeness": state.get("field_completeness"),
                "cross_validation_score": state.get("cross_validation_score"),
                "issues": state.get("validation_issues", []),
            },
            reasoning=f"Confidence: {state.get('confidence_score', 0):.3f}",
        )

    # Resolve log
    if state.get("resolve_attempted"):
        await _log_stage(
            db, doc_uuid, PipelineStage.RESOLVE,
            StageStatus.COMPLETED if state.get("resolve_resolved") else StageStatus.FAILED,
            duration_ms=timings.get("resolve"),
            metadata={"resolved": state.get("resolve_resolved")},
            reasoning=state.get("resolve_reasoning"),
            agent_steps=state.get("resolve_agent_steps"),
        )


# ── Main entry point ───────────────────────────────────────────────────────────

async def process_document(document_id: str) -> None:
    """
    Run the LangGraph pipeline on a document.

    Called by the Celery task worker. Opens its own DB session.
    """
    doc_uuid = uuid.UUID(document_id)

    async with async_session_factory() as db:
        try:
            # ── RC-1: Atomic ownership claim ──────────────────────────────────
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
                return

            # ── Invoke LangGraph pipeline ─────────────────────────────────────
            initial_state: PipelineState = {
                "document_id": document_id,
                "file_path": str(document.file_path),
            }

            final_state = await pipeline.ainvoke(initial_state)

            # ── Persist results to DB ─────────────────────────────────────────

            # Update document fields
            if final_state.get("raw_text"):
                document.raw_text = final_state["raw_text"]
            if final_state.get("document_type"):
                document.document_type = final_state["document_type"]
            if final_state.get("extracted_data"):
                document.extracted_data = final_state["extracted_data"]
                await _write_extracted_fields(db, doc_uuid, final_state["extracted_data"])
            if final_state.get("error"):
                document.confidence_score = None
            elif final_state.get("confidence_score") is not None:
                document.confidence_score = final_state["confidence_score"]

            # Set final status
            final_status = final_state.get("final_status", "failed")
            document.status = STATUS_MAP.get(final_status, DocumentStatus.FAILED)
            document.processed_at = datetime.now(timezone.utc)

            # Persist stage logs
            await _persist_pipeline_logs(db, doc_uuid, final_state)

            await db.commit()

            # ── Update full-text search vector (non-fatal) ────────────────────
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
                pass  # Non-fatal

            # ── Index to Elasticsearch (non-fatal) ────────────────────────────
            try:
                from app.elasticsearch import index_document
                from app.utils.llm import get_embeddings
                
                # Generate semantic summary
                summary_parts = [f"Filename: {document.original_filename}", f"Type: {document.document_type}"]
                if document.extracted_data:
                    import json
                    summary_parts.append(f"Data: {json.dumps(document.extracted_data)}")
                summary_parts.append(f"Content: {(document.raw_text or '')[:1000]}")
                summary_text = " | ".join(summary_parts)
                
                # Generate embeddings
                document_vector = None
                try:
                    embeddings = get_embeddings()
                    document_vector = await embeddings.aembed_query(summary_text)
                except Exception as e:
                    import logging
                    logging.warning(f"Failed to generate document vector for {doc_uuid}: {e}")

                await index_document(
                    document_id=str(doc_uuid),
                    original_filename=document.original_filename,
                    document_type=document.document_type,
                    status=document.status.value,
                    confidence_score=document.confidence_score,
                    raw_text=document.raw_text,
                    extracted_data=document.extracted_data,
                    document_vector=document_vector,
                    created_at=document.created_at,
                )
            except Exception as e:
                import logging
                logging.warning(f"Failed ES indexing for {doc_uuid}: {e}")
                pass  # Non-fatal — ES may not be running

        except Exception as e:
            import logging
            logging.error(f"Pipeline error for {doc_uuid}: {e}", exc_info=True)
            # Catch-all safety net
            try:
                await db.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .where(Document.status == DocumentStatus.PROCESSING)
                    .values(
                        status=DocumentStatus.FAILED,
                        confidence_score=None,
                        processed_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
            except Exception:
                pass
