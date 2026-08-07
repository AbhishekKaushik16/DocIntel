"""
Pipeline Orchestrator — Adaptive Agentic Loop

Coordinates the processing pipeline for a single document:

  1. PARSE     — extract text from the raw file
  2. CLASSIFY  — Classifier Agent (tool-using) determines document type
  3. EXTRACT   — LLM extracts structured data
  4. VALIDATE  — cross-field validation + confidence scoring
  5. RESOLVE   — Resolver Agent attempts to fix validation failures (optional)
  6. VALIDATE  — re-scores after resolution (one retry max)

Branching logic:
  • If VALIDATE has no actionable errors/warnings → route to COMPLETED/NEEDS_REVIEW
  • If VALIDATE has warnings/errors → invoke RESOLVE agent
  • After RESOLVE: re-run VALIDATE; always route based on final confidence score

Agent reasoning and tool-call traces are persisted to processing_logs.reasoning
and processing_logs.agent_steps for full observability into *why* each decision
was made — not just what the outcome was.

Concurrency guarantees
──────────────────────
• RC-1  Atomic ownership claim: single UPDATE … WHERE status IN (…) RETURNING *
        One session only to avoid asyncpg "Future attached to different loop" error.

• RC-2  Idempotent field writes: DELETE before INSERT in the extract stage.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, text, update
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
from app.pipeline.validator import validate_and_score, ValidationResult
from app.pipeline.resolver import resolve_validation_issues


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
    """
    Write a processing log entry.

    Flushed but not committed — caller must commit.
    Includes 'reasoning' (agent's chain-of-thought) and 'agent_steps'
    (full tool-call trace) for agentic stages.
    """
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


def _issues_to_dicts(issues) -> list[dict]:
    """Convert ValidationIssue dataclass list to JSON-serializable dicts."""
    return [{"field": i.field, "severity": i.severity, "message": i.message} for i in issues]


def _has_actionable_issues(validation: ValidationResult) -> bool:
    """
    Returns True only if there are ERROR-severity issues worth invoking the
    Resolver Agent for. WARNING-severity issues (missing optional fields, low
    confidence) are handled by routing to NEEDS_REVIEW directly — the Resolver
    is reserved for fixable data errors like arithmetic mismatches.
    """
    return any(i.severity == "error" for i in validation.issues)


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


# ── Main entry point ───────────────────────────────────────────────────────────

async def process_document(document_id: str) -> None:
    """
    Run the adaptive extraction pipeline on a document.

    Called by the Celery task worker. Opens its own DB session.

    Stage flow:
      PARSE → CLASSIFY (agent) → EXTRACT → VALIDATE
        └─ if issues → RESOLVE (agent) → VALIDATE (retry)

    All agent reasoning and tool traces are stored in processing_logs.
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
            print(f"[DEBUG ORCHESTRATOR] ID={doc_uuid}, claimed document={document}")
            if document is None:
                # DEBUG WHY IT'S NONE
                debug_res = await db.execute(select(Document.status).where(Document.id == doc_uuid))
                status = debug_res.scalar_one_or_none()
                print(f"[DEBUG ORCHESTRATOR] Current status in DB: {status}")
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

            # ── Stage 2: Classify (Agent) ─────────────────────────────────────
            # The Classifier Agent uses tools (check_file_metadata, sample_page_text,
            # run_ocr_preview) iteratively before committing to a document type.
            # Its reasoning and tool-call trace are persisted to processing_logs.
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.STARTED)

            try:
                # Pass file_path, not text — the agent decides how much to sample
                classification = await classify_document(document.file_path)
                duration = int((time.monotonic() - t0) * 1000)

                document.document_type = classification.document_type
                await _log_stage(
                    db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={
                        "type": classification.document_type,
                        "confidence": classification.confidence,
                        "method": classification.method,
                        "tool_rounds": len(classification.agent_steps),
                    },
                    reasoning=classification.reasoning,
                    agent_steps=classification.agent_steps,
                )
                await db.commit()

            except Exception as e:
                duration = int((time.monotonic() - t0) * 1000)
                await _log_stage(
                    db, doc_uuid, PipelineStage.CLASSIFY, StageStatus.FAILED,
                    duration_ms=duration,
                    error_message=str(e),
                    reasoning="Classifier agent failed; falling back to 'generic' type.",
                )
                document.document_type = document.document_type or "generic"
                await db.commit()

            # ── Stage 3: Extract ──────────────────────────────────────────────
            t0 = time.monotonic()
            await _log_stage(db, doc_uuid, PipelineStage.EXTRACT, StageStatus.STARTED)

            try:
                extracted_data, extraction_method = await extract_structured_data(
                    parse_result.text,
                    document.document_type,
                    file_path=str(document.file_path),
                )
                duration = int((time.monotonic() - t0) * 1000)

                document.extracted_data = extracted_data
                await _log_stage(
                    db, doc_uuid, PipelineStage.EXTRACT, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={"method": extraction_method},
                )

                await _write_extracted_fields(db, doc_uuid, extracted_data)
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

                issues_dicts = _issues_to_dicts(validation.issues)
                await _log_stage(
                    db, doc_uuid, PipelineStage.VALIDATE, StageStatus.COMPLETED,
                    duration_ms=duration,
                    metadata={
                        "confidence_score": validation.confidence_score,
                        "field_completeness": validation.field_completeness,
                        "cross_validation_score": validation.cross_validation_score,
                        "issues": issues_dicts,
                    },
                    reasoning=(
                        f"Validation found {len(issues_dicts)} issue(s). "
                        f"Confidence: {validation.confidence_score:.3f}. "
                        + ("Will invoke Resolver Agent." if _has_actionable_issues(validation) else "No actionable issues.")
                    ),
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
                return

            # ── Stage 5: Resolve (Agent — conditional) ────────────────────────
            # The Resolver Agent fires when validation found fixable issues.
            # It uses tools to re-examine problematic fields and returns corrected data.
            # After resolution we re-run validation once (max one retry loop).
            if _has_actionable_issues(validation):
                t0 = time.monotonic()
                await _log_stage(
                    db, doc_uuid, PipelineStage.RESOLVE, StageStatus.STARTED,
                    reasoning=(
                        f"Resolver Agent triggered for {len(issues_dicts)} validation issue(s). "
                        f"Using model: {settings.strong_model_name}."
                    ),
                )

                try:
                    resolver_result = await resolve_validation_issues(
                        raw_text=parse_result.text,
                        document_type=document.document_type,
                        extracted_data=extracted_data,
                        issues=issues_dicts,
                    )
                    duration = int((time.monotonic() - t0) * 1000)

                    await _log_stage(
                        db, doc_uuid, PipelineStage.RESOLVE,
                        StageStatus.COMPLETED if resolver_result.resolved else StageStatus.FAILED,
                        duration_ms=duration,
                        metadata={
                            "resolved": resolver_result.resolved,
                            "corrections_applied": list(
                                set(resolver_result.extracted_data.keys()) -
                                set(extracted_data.keys()) |
                                {k for k in resolver_result.extracted_data
                                 if resolver_result.extracted_data[k] != extracted_data.get(k)}
                            ),
                            "tool_rounds": len(resolver_result.agent_steps),
                        },
                        reasoning=resolver_result.reasoning,
                        agent_steps=resolver_result.agent_steps,
                    )

                    if resolver_result.resolved:
                        # Apply corrections and re-run validation
                        extracted_data = resolver_result.extracted_data
                        document.extracted_data = extracted_data
                        await _write_extracted_fields(db, doc_uuid, extracted_data)
                        await db.commit()

                        # ── Re-validate after resolution ──────────────────────
                        t0 = time.monotonic()
                        await _log_stage(
                            db, doc_uuid, PipelineStage.VALIDATE, StageStatus.STARTED,
                            reasoning="Re-validating after Resolver Agent applied corrections.",
                        )
                        validation = validate_and_score(
                            extracted_data=extracted_data,
                            document_type=document.document_type,
                            extraction_method=extraction_method,
                            parse_warnings=parse_result.warnings,
                        )
                        duration = int((time.monotonic() - t0) * 1000)
                        re_issues = _issues_to_dicts(validation.issues)
                        await _log_stage(
                            db, doc_uuid, PipelineStage.VALIDATE, StageStatus.COMPLETED,
                            duration_ms=duration,
                            metadata={
                                "confidence_score": validation.confidence_score,
                                "field_completeness": validation.field_completeness,
                                "cross_validation_score": validation.cross_validation_score,
                                "issues": re_issues,
                                "post_resolution": True,
                            },
                            reasoning=(
                                f"Post-resolution validation: confidence={validation.confidence_score:.3f}, "
                                f"{len(re_issues)} remaining issue(s)."
                            ),
                        )
                        await db.commit()
                    else:
                        await db.commit()

                except Exception as e:
                    duration = int((time.monotonic() - t0) * 1000)
                    await _log_stage(
                        db, doc_uuid, PipelineStage.RESOLVE, StageStatus.FAILED,
                        duration_ms=duration,
                        error_message=str(e),
                        reasoning=f"Resolver Agent raised an exception: {str(e)[:200]}",
                    )
                    await db.commit()
                    # Continue with original validation score — don't fail the document

            # ── Final routing decision (always runs, even after resolver errors) ──
            try:
                document.confidence_score = validation.confidence_score
                document.processed_at = datetime.now(timezone.utc)

                if validation.confidence_score >= settings.confidence_auto_approve:
                    document.status = DocumentStatus.COMPLETED
                elif validation.confidence_score >= settings.confidence_review_threshold:
                    document.status = DocumentStatus.NEEDS_REVIEW
                else:
                    document.status = DocumentStatus.FAILED

                await db.commit()
            except Exception as routing_err:
                # Safety net: document must never stay PROCESSING
                await db.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .where(Document.status == DocumentStatus.PROCESSING)
                    .values(
                        status=DocumentStatus.NEEDS_REVIEW,
                        processed_at=datetime.now(timezone.utc),
                    )
                )
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
                pass  # Non-fatal — search index may be stale but data is safe

        except Exception as e:
            import logging
            logging.error(f"[DEBUG ORCHESTRATOR] Catch-all Exception: {e}", exc_info=True)
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
