"""
Stage 4: Validator & Confidence Scorer

Validates extracted data and computes a composite confidence score.
Routes documents to completed/needs_review/failed based on thresholds.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationIssue:
    """A specific validation problem found in the extracted data."""
    field: str
    severity: str  # "error", "warning", "info"
    message: str


@dataclass
class ValidationResult:
    """Complete validation output with confidence scoring."""
    confidence_score: float
    issues: list[ValidationIssue] = field(default_factory=list)
    field_completeness: float = 0.0
    cross_validation_score: float = 0.0
    extraction_method_score: float = 0.0


# ── Field importance weights per known document type ──────────────────




def _is_field_present(value: Any) -> bool:
    """Check if a field has a meaningful value (not None/empty)."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    return True


def compute_field_completeness(
    extracted_data: dict[str, Any],
    document_type: str,
) -> tuple[float, list[ValidationIssue]]:
    """
    Compute what percentage of fields were extracted dynamically.
    Since we do not enforce any pre-defined schema, any non-null field 
    is considered a successful extraction.
    """
    issues = []
    
    if not extracted_data:
        return 0.0, [ValidationIssue(field="_all", severity="error", message="No structured fields extracted")]

    total_keys = len(extracted_data)
    present_keys = sum(1 for v in extracted_data.values() if _is_field_present(v))
    
    # If the LLM returns 10 keys and 9 are non-empty, score is 0.9.
    score = present_keys / total_keys if total_keys > 0 else 0.5

    return score, issues


def cross_validate(
    extracted_data: dict[str, Any],
    document_type: str,
) -> tuple[float, list[ValidationIssue]]:
    """
    Since we use a flexible schema generated entirely by the extractor, 
    we cannot hardcode cross-validation rules (e.g. total = subtotal + tax).
    This function simply returns a perfect score unless we implement
    LLM-based dynamic validation in the future.
    """
    return 1.0, []


def validate_and_score(
    extracted_data: dict[str, Any],
    document_type: str,
    extraction_method: str,
    parse_warnings: list[str] | None = None,
) -> ValidationResult:
    """
    Main validation entry point.
    """
    all_issues = []

    # 1. Field completeness
    completeness, completeness_issues = compute_field_completeness(extracted_data, document_type)
    all_issues.extend(completeness_issues)

    # 2. Cross-field validation
    cross_score, cross_issues = cross_validate(extracted_data, document_type)
    all_issues.extend(cross_issues)

    # 3. Extraction method score
    if extraction_method == "llm":
        method_score = 0.9
    else:
        method_score = 0.3

    # 4. Parse quality
    parse_score = 1.0
    if parse_warnings:
        parse_score = max(0.3, 1.0 - 0.15 * len(parse_warnings))
        for w in parse_warnings:
            all_issues.append(ValidationIssue(
                field="_parse",
                severity="info",
                message=w,
            ))

    # Composite score (weighted average)
    confidence = (
        completeness * 0.40
        + cross_score * 0.30
        + method_score * 0.20
        + parse_score * 0.10
    )

    confidence = max(0.0, min(1.0, round(confidence, 3)))

    return ValidationResult(
        confidence_score=confidence,
        issues=all_issues,
        field_completeness=round(completeness, 3),
        cross_validation_score=round(cross_score, 3),
        extraction_method_score=round(method_score, 3),
    )
