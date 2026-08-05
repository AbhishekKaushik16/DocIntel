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

REQUIRED_FIELDS: dict[str, list[str]] = {
    "invoice": [
        "invoice_number", "invoice_date", "vendor_name", "total_amount",
    ],
    "receipt": [
        "store_name", "transaction_date", "total_amount",
    ],
    "contract": [
        "contract_title", "parties", "effective_date",
    ],
    "resume": [
        "full_name", "email", "work_experience",
    ],
    "generic": [
        "summary",
    ],
}

OPTIONAL_FIELDS: dict[str, list[str]] = {
    "invoice": [
        "due_date", "vendor_address", "bill_to_name", "bill_to_address",
        "line_items", "subtotal", "tax_amount", "currency", "payment_terms",
    ],
    "receipt": [
        "store_address", "transaction_id", "items", "subtotal",
        "tax_amount", "payment_method",
    ],
    "contract": [
        "expiration_date", "contract_value", "key_terms", "governing_law", "summary",
    ],
    "resume": [
        "phone", "location", "summary", "skills", "education", "linkedin_url",
    ],
    "generic": [
        "title", "author", "date", "key_entities", "amounts", "emails",
    ],
}


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
    Compute what percentage of fields were extracted.

    For known types, uses weighted required/optional field rules.
    For custom/dynamic types, calculates non-null fields over total extracted fields.
    """
    issues = []
    doc_type_lower = (document_type or "").lower()

    if (doc_type_lower in REQUIRED_FIELDS or doc_type_lower in OPTIONAL_FIELDS) and not (
        doc_type_lower == "generic" and extracted_data and "summary" not in extracted_data
    ):
        required = REQUIRED_FIELDS.get(doc_type_lower, [])
        optional = OPTIONAL_FIELDS.get(doc_type_lower, [])

        required_present = 0
        for f in required:
            if _is_field_present(extracted_data.get(f)):
                required_present += 1
            else:
                issues.append(ValidationIssue(
                    field=f,
                    severity="warning",
                    message=f"Required field '{f}' is missing or empty",
                ))

        optional_present = sum(1 for f in optional if _is_field_present(extracted_data.get(f)))

        total_weight = len(required) * 2 + len(optional)
        present_weight = required_present * 2 + optional_present

        score = present_weight / total_weight if total_weight > 0 else 0.5
        return score, issues

    # Dynamic/custom document types
    if not extracted_data:
        return 0.0, [ValidationIssue(field="_all", severity="error", message="No structured fields extracted")]

    total_keys = len(extracted_data)
    present_keys = sum(1 for v in extracted_data.values() if _is_field_present(v))
    score = present_keys / total_keys if total_keys > 0 else 0.5

    return score, issues


def cross_validate_invoice(data: dict[str, Any]) -> tuple[float, list[ValidationIssue]]:
    """
    Cross-field validation for invoices.
    """
    issues = []
    checks_passed = 0
    checks_total = 0

    total = data.get("total_amount")
    subtotal = data.get("subtotal")
    tax = data.get("tax_amount")

    # Check 1: total ≈ subtotal + tax
    if total is not None and subtotal is not None and tax is not None:
        checks_total += 1
        expected = subtotal + tax
        if abs(total - expected) <= 0.02 * max(abs(total), 1):
            checks_passed += 1
        else:
            issues.append(ValidationIssue(
                field="total_amount",
                severity="warning",
                message=f"Total ({total}) doesn't match subtotal + tax ({expected}). Possible extraction error.",
            ))

    # Check 2: total ≈ sum of line items
    line_items = data.get("line_items", [])
    if total is not None and line_items:
        checks_total += 1
        line_sum = sum(
            item.get("amount", 0) or 0
            for item in line_items
            if isinstance(item, dict)
        )
        if line_sum > 0:
            if abs(total - line_sum) <= 0.05 * max(abs(total), 1):
                checks_passed += 1
            else:
                issues.append(ValidationIssue(
                    field="line_items",
                    severity="warning",
                    message=f"Sum of line items ({line_sum}) doesn't match total ({total}).",
                ))

    # Check 3: due_date >= invoice_date
    invoice_date = data.get("invoice_date")
    due_date = data.get("due_date")
    if invoice_date and due_date:
        checks_total += 1
        try:
            if due_date >= invoice_date:
                checks_passed += 1
            else:
                issues.append(ValidationIssue(
                    field="due_date",
                    severity="warning",
                    message=f"Due date ({due_date}) is before invoice date ({invoice_date}).",
                ))
        except Exception:
            pass

    score = checks_passed / checks_total if checks_total > 0 else 0.7
    return score, issues


def cross_validate(
    extracted_data: dict[str, Any],
    document_type: str,
) -> tuple[float, list[ValidationIssue]]:
    """Route to type-specific cross-validation."""
    doc_type_lower = (document_type or "").lower()
    if doc_type_lower == "invoice":
        return cross_validate_invoice(extracted_data)

    return 0.7, []


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
    elif extraction_method.startswith("regex_fallback"):
        method_score = 0.5
    elif extraction_method == "regex":
        method_score = 0.4
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
