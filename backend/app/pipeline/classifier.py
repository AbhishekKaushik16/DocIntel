"""
Stage 1: Document Classifier

Determines the document type (invoice, receipt, contract, resume, generic)
using a two-pass approach:
  1. Fast heuristic pass (keyword + structure analysis)
  2. LLM classification fallback for ambiguous documents
"""

import re
from dataclasses import dataclass

from app.models.document import DocumentType


@dataclass
class ClassificationResult:
    """Result of document classification."""
    document_type: DocumentType
    confidence: float
    method: str  # "heuristic" or "llm"


# Keyword patterns for each document type (case-insensitive)
KEYWORD_PATTERNS: dict[DocumentType, list[str]] = {
    DocumentType.INVOICE: [
        r"\binvoice\b", r"\binv[\s#\-.:]*\d+", r"\bbill\s+to\b",
        r"\btotal\s+due\b", r"\bamount\s+due\b", r"\bpayment\s+terms\b",
        r"\bdue\s+date\b", r"\bsubtotal\b", r"\btax\b.*\bamount\b",
        r"\bpurchase\s+order\b", r"\bpo[\s#\-.:]*\d+",
        r"\btax\s+invoice\b", r"\bbill\s+of\s+supply\b",
        r"\binvoice\s+number\b", r"\binvoice\s+date\b",
        r"\border\s+number\b", r"\bgst\s+registration\s+no\b",
        r"\bpan\s+no\b",
    ],
    DocumentType.RECEIPT: [
        r"\breceipt\b", r"\btransaction\b", r"\bpaid\b",
        r"\bchange\s+due\b", r"\bcard\s+ending\b", r"\btotal\s*[:$]",
        r"\bsubtotal\s*[:$]", r"\bthank\s+you\s+for\s+your\s+purchase\b",
        r"\bstore\s*#", r"\bcashier\b",
    ],
    DocumentType.CONTRACT: [
        r"\bagreement\b", r"\bcontract\b", r"\bterms\s+and\s+conditions\b",
        r"\bhereby\b", r"\bwhereas\b", r"\bparty\b.*\bparty\b",
        r"\beffective\s+date\b", r"\btermination\b", r"\bclause\b",
        r"\bsignature\b", r"\bexecuted\b", r"\bindemnif",
    ],
    DocumentType.RESUME: [
        r"\bresume\b", r"\bcurriculum\s+vitae\b", r"\bcv\b",
        r"\bwork\s+experience\b", r"\beducation\b.*\buniversity\b",
        r"\bskills\b", r"\bemployment\s+history\b", r"\bprofessional\s+summary\b",
        r"\breferences\s+available\b", r"\blinkedin\.com\b",
        r"\bobjective\b", r"\bqualifications\b",
    ],
}

# Minimum score to consider a heuristic classification confident
HEURISTIC_CONFIDENCE_THRESHOLD = 0.25


def classify_by_heuristic(text: str) -> ClassificationResult:
    """
    Classify a document using keyword pattern matching.

    Scores each document type by counting matching patterns,
    normalized by the total number of patterns for that type.
    """
    if not text or not text.strip():
        return ClassificationResult(
            document_type=DocumentType.GENERIC,
            confidence=0.3,
            method="heuristic",
        )

    text_lower = text.lower()
    scores: dict[DocumentType, float] = {}
    match_counts: dict[DocumentType, int] = {}

    for doc_type, patterns in KEYWORD_PATTERNS.items():
        matches = sum(1 for p in patterns if re.search(p, text_lower))
        match_counts[doc_type] = matches
        scores[doc_type] = matches / len(patterns) if patterns else 0

    if not scores:
        return ClassificationResult(
            document_type=DocumentType.GENERIC,
            confidence=0.3,
            method="heuristic",
        )

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]
    best_matches = match_counts.get(best_type, 0)

    # Retail tax invoices often say "Tax Invoice/Bill of Supply" and contain
    # order/GST identifiers instead of classic "amount due" wording.
    if best_type == DocumentType.INVOICE and best_matches >= 3:
        return ClassificationResult(
            document_type=DocumentType.INVOICE,
            confidence=min(0.9, 0.45 + best_matches * 0.08),
            method="heuristic",
        )

    # Resumes often omit the literal word "resume"; a couple of strong section
    # signals are enough to identify them.
    if best_type == DocumentType.RESUME and best_matches >= 2:
        return ClassificationResult(
            document_type=DocumentType.RESUME,
            confidence=min(0.85, 0.45 + best_matches * 0.1),
            method="heuristic",
        )

    # If no type scored above threshold, classify as generic
    if best_score < HEURISTIC_CONFIDENCE_THRESHOLD:
        # Check if there's at least some signal
        if best_score > 0.2:
            return ClassificationResult(
                document_type=best_type,
                confidence=best_score,
                method="heuristic",
            )
        return ClassificationResult(
            document_type=DocumentType.GENERIC,
            confidence=0.4,
            method="heuristic",
        )

    return ClassificationResult(
        document_type=best_type,
        confidence=min(0.95, best_score + 0.3),  # Boost confident matches
        method="heuristic",
    )


async def classify_by_llm(text: str) -> ClassificationResult:
    """
    Classify a document using LLM when heuristic is ambiguous.

    Uses a short, focused prompt to minimize token usage.
    """
    from app.config import settings

    if not settings.llm_available:
        # Fall back to generic if LLM is not available
        return ClassificationResult(
            document_type=DocumentType.GENERIC,
            confidence=0.3,
            method="heuristic_fallback",
        )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        # Use only the first ~2000 chars to minimize cost
        truncated_text = text[:2000]

        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a document classifier. Classify the document into exactly one of: "
                        "invoice, receipt, contract, resume, generic. "
                        "Respond with ONLY the type name in lowercase, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Classify this document:\n\n{truncated_text}",
                },
            ],
            temperature=0,
            max_tokens=10,
        )

        type_str = response.choices[0].message.content.strip().lower()

        # Map response to enum
        type_map = {t.value: t for t in DocumentType}
        doc_type = type_map.get(type_str, DocumentType.GENERIC)

        return ClassificationResult(
            document_type=doc_type,
            confidence=0.85,
            method="llm",
        )

    except Exception:
        # LLM failed — fall back to heuristic result
        return classify_by_heuristic(text)


async def classify_document(text: str) -> ClassificationResult:
    """
    Main classification entry point.

    Strategy:
    1. Try heuristic classification first (fast, free)
    2. If confidence is low, escalate to LLM (slower, costs tokens)
    """
    heuristic_result = classify_by_heuristic(text)

    if heuristic_result.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD:
        return heuristic_result

    # Heuristic wasn't confident enough — try LLM
    llm_result = await classify_by_llm(text)

    # Return whichever is more confident
    if llm_result.confidence > heuristic_result.confidence:
        return llm_result

    return heuristic_result
