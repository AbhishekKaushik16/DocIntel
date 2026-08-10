"""
LangGraph Pipeline — Declarative State Machine for Document Processing

Replaces the hand-rolled orchestrator with a typed StateGraph where each
pipeline stage is an independently testable node:

  parse → classify → extract → validate ─┬─→ finalize
                                          └─→ resolve → validate → finalize

Key benefits over the old orchestrator:
  • Typed state (PipelineState TypedDict)
  • Declarative conditional routing
  • Each node is a pure async function (easy to unit-test)
  • Built-in error propagation without nested try/except
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from langgraph.graph import StateGraph, END

from app.config import settings

logger = logging.getLogger(__name__)


# ── Pipeline State ─────────────────────────────────────────────────────────────

class PipelineState(TypedDict, total=False):
    """Typed state that flows through every node in the graph."""
    # Input
    document_id: str
    file_path: str

    # Parse output
    raw_text: str
    parse_method: str
    page_count: int
    parse_warnings: list[str]
    parse_metadata: dict[str, Any]

    # Classify output
    document_type: str
    classify_confidence: float
    classify_method: str
    classify_reasoning: str
    classify_agent_steps: list[dict[str, Any]]

    # Extract output
    extracted_data: dict[str, Any]
    extraction_method: str

    # Validate output
    confidence_score: float
    field_completeness: float
    cross_validation_score: float
    extraction_method_score: float
    validation_issues: list[dict[str, Any]]

    # Resolve output
    resolve_attempted: bool
    resolve_resolved: bool
    resolve_reasoning: str
    resolve_agent_steps: list[dict[str, Any]]

    # Finalize
    final_status: str

    # Timing & errors
    stage_timings: dict[str, int]
    error: str | None


# ── Node implementations ──────────────────────────────────────────────────────

async def parse_node(state: PipelineState) -> dict:
    """Stage 1: Parse — extract text from the raw file."""
    from app.pipeline.parser import parse_document

    t0 = time.monotonic()
    try:
        result = await parse_document(state["file_path"])
        duration = int((time.monotonic() - t0) * 1000)

        if not result.text.strip():
            return {
                "error": "No text could be extracted from the document.",
                "stage_timings": {**state.get("stage_timings", {}), "parse": duration},
            }

        return {
            "raw_text": result.text,
            "parse_method": result.method,
            "page_count": result.page_count,
            "parse_warnings": result.warnings,
            "parse_metadata": result.metadata,
            "stage_timings": {**state.get("stage_timings", {}), "parse": duration},
        }
    except Exception as e:
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "error": f"Parse failed: {str(e)}",
            "stage_timings": {**state.get("stage_timings", {}), "parse": duration},
        }


async def classify_node(state: PipelineState) -> dict:
    """Stage 2: Classify — determine the document type using an LLM agent."""
    from app.pipeline.classifier import classify_document

    t0 = time.monotonic()
    try:
        result = await classify_document(state["file_path"])
        duration = int((time.monotonic() - t0) * 1000)

        return {
            "document_type": result.document_type,
            "classify_confidence": result.confidence,
            "classify_method": result.method,
            "classify_reasoning": result.reasoning,
            "classify_agent_steps": result.agent_steps,
            "stage_timings": {**state.get("stage_timings", {}), "classify": duration},
        }
    except Exception as e:
        duration = int((time.monotonic() - t0) * 1000)
        logger.warning(f"Classifier failed, falling back to 'generic': {e}")
        return {
            "document_type": "generic",
            "classify_confidence": 0.3,
            "classify_method": "fallback",
            "classify_reasoning": f"Classifier agent failed: {str(e)[:200]}",
            "classify_agent_steps": [],
            "stage_timings": {**state.get("stage_timings", {}), "classify": duration},
        }


async def extract_node(state: PipelineState) -> dict:
    """Stage 3: Extract — LLM extracts structured data from parsed text."""
    from app.pipeline.extractor import extract_structured_data

    t0 = time.monotonic()
    try:
        data, method = await extract_structured_data(
            state["raw_text"],
            state["document_type"],
            file_path=state.get("file_path", ""),
        )
        duration = int((time.monotonic() - t0) * 1000)

        return {
            "extracted_data": data,
            "extraction_method": method,
            "stage_timings": {**state.get("stage_timings", {}), "extract": duration},
        }
    except Exception as e:
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "error": f"Extraction failed: {str(e)}",
            "stage_timings": {**state.get("stage_timings", {}), "extract": duration},
        }


async def validate_node(state: PipelineState) -> dict:
    """Stage 4: Validate — cross-field validation + confidence scoring."""
    from app.pipeline.validator import validate_and_score

    t0 = time.monotonic()
    try:
        result = validate_and_score(
            extracted_data=state["extracted_data"],
            document_type=state["document_type"],
            extraction_method=state["extraction_method"],
            parse_warnings=state.get("parse_warnings"),
        )
        duration = int((time.monotonic() - t0) * 1000)

        issues = [
            {"field": i.field, "severity": i.severity, "message": i.message}
            for i in result.issues
        ]

        return {
            "confidence_score": result.confidence_score,
            "field_completeness": result.field_completeness,
            "cross_validation_score": result.cross_validation_score,
            "extraction_method_score": result.extraction_method_score,
            "validation_issues": issues,
            "stage_timings": {**state.get("stage_timings", {}), "validate": duration},
        }
    except Exception as e:
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "confidence_score": 0.5,
            "validation_issues": [],
            "stage_timings": {**state.get("stage_timings", {}), "validate": duration},
        }


async def resolve_node(state: PipelineState) -> dict:
    """Stage 5: Resolve — agent attempts to fix validation errors."""
    from app.pipeline.resolver import resolve_validation_issues

    t0 = time.monotonic()
    try:
        result = await resolve_validation_issues(
            raw_text=state["raw_text"],
            document_type=state["document_type"],
            extracted_data=state["extracted_data"],
            issues=state.get("validation_issues", []),
        )
        duration = int((time.monotonic() - t0) * 1000)

        updates = {
            "resolve_attempted": True,
            "resolve_resolved": result.resolved,
            "resolve_reasoning": result.reasoning,
            "resolve_agent_steps": result.agent_steps,
            "stage_timings": {**state.get("stage_timings", {}), "resolve": duration},
        }

        if result.resolved:
            updates["extracted_data"] = result.extracted_data

        return updates
    except Exception as e:
        duration = int((time.monotonic() - t0) * 1000)
        return {
            "resolve_attempted": True,
            "resolve_resolved": False,
            "resolve_reasoning": f"Resolver agent failed: {str(e)[:200]}",
            "resolve_agent_steps": [],
            "stage_timings": {**state.get("stage_timings", {}), "resolve": duration},
        }


async def finalize_node(state: PipelineState) -> dict:
    """Final node — determine the document status based on confidence score."""
    score = state.get("confidence_score", 0.0)

    if state.get("error"):
        return {"final_status": "failed"}
    elif score >= settings.confidence_auto_approve:
        return {"final_status": "completed"}
    elif score >= settings.confidence_review_threshold:
        return {"final_status": "needs_review"}
    else:
        return {"final_status": "failed"}


# ── Conditional routing ────────────────────────────────────────────────────────

def check_parse_error(state: PipelineState) -> Literal["classify", "finalize"]:
    """After parse: if there's an error, skip to finalize."""
    if state.get("error"):
        return "finalize"
    return "classify"


def check_extract_error(state: PipelineState) -> Literal["validate", "finalize"]:
    """After extract: if there's an error, skip to finalize."""
    if state.get("error"):
        return "finalize"
    return "validate"


def should_resolve(state: PipelineState) -> Literal["resolve", "finalize"]:
    """After validate: route to resolve if there are error-severity issues."""
    issues = state.get("validation_issues", [])
    has_errors = any(i.get("severity") == "error" for i in issues)
    if has_errors:
        return "resolve"
    return "finalize"


def post_resolve_route(state: PipelineState) -> Literal["re_validate", "finalize"]:
    """After resolve: re-validate if corrections were applied."""
    if state.get("resolve_resolved"):
        return "re_validate"
    return "finalize"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    """Construct and compile the LangGraph pipeline."""
    graph = StateGraph(PipelineState)

    # Add nodes
    graph.add_node("parse", parse_node)
    graph.add_node("classify", classify_node)
    graph.add_node("extract", extract_node)
    graph.add_node("validate", validate_node)
    graph.add_node("resolve", resolve_node)
    graph.add_node("re_validate", validate_node)  # Same function, different node name
    graph.add_node("finalize", finalize_node)

    # Set entry point
    graph.set_entry_point("parse")

    # Edges
    graph.add_conditional_edges("parse", check_parse_error)
    graph.add_edge("classify", "extract")
    graph.add_conditional_edges("extract", check_extract_error)
    graph.add_conditional_edges("validate", should_resolve)
    graph.add_conditional_edges("resolve", post_resolve_route)
    graph.add_edge("re_validate", "finalize")
    graph.add_edge("finalize", END)

    return graph


# Compiled pipeline instance (without checkpointer, for backward compatibility / tests)
pipeline = build_pipeline().compile()
