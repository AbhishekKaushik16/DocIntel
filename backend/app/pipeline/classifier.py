"""
Stage 1: Document Classifier

Determines the document type using a two-pass approach:
  1. Fast heuristic pass (keyword + structure analysis for common types)
  2. Flexible LLM classification fallback for dynamic, open-ended classification
"""

import json
import re
from dataclasses import dataclass

from app.config import settings


@dataclass
class ClassificationResult:
    """Result of document classification."""

    document_type: str
    confidence: float
    method: str  # "heuristic" or "llm"


# Keyword patterns for common document types (case-insensitive)
KEYWORD_PATTERNS: dict[str, list[str]] = {
    "invoice": [
        r"\binvoice\b", r"\binv[\s#\-.:]*\d+", r"\bbill\s+to\b",
        r"\btotal\s+due\b", r"\bamount\s+due\b", r"\bpayment\s+terms\b",
        r"\bdue\s+date\b", r"\bsubtotal\b", r"\btax\b.*\bamount\b",
        r"\bpurchase\s+order\b", r"\bpo[\s#\-.:]*\d+",
        r"\btax\s+invoice\b", r"\bbill\s+of\s+supply\b",
        r"\binvoice\s+number\b", r"\binvoice\s+date\b",
        r"\border\s+number\b", r"\bgst\s+registration\s+no\b",
        r"\bpan\s+no\b",
    ],
    "receipt": [
        r"\breceipt\b", r"\btransaction\b", r"\bpaid\b",
        r"\bchange\s+due\b", r"\bcard\s+ending\b", r"\btotal\s*[:$]",
        r"\bsubtotal\s*[:$]", r"\bthank\s+you\s+for\s+your\s+purchase\b",
        r"\bstore\s*#", r"\bcashier\b",
    ],
    "contract": [
        r"\bagreement\b", r"\bcontract\b", r"\bterms\s+and\s+conditions\b",
        r"\bhereby\b", r"\bwhereas\b", r"\bparty\b.*\bparty\b",
        r"\beffective\s+date\b", r"\btermination\b", r"\bclause\b",
        r"\bsignature\b", r"\bexecuted\b", r"\bindemnif",
    ],
    "resume": [
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
            document_type="generic",
            confidence=0.3,
            method="heuristic",
        )

    text_lower = text.lower()
    scores: dict[str, float] = {}
    match_counts: dict[str, int] = {}

    for doc_type, patterns in KEYWORD_PATTERNS.items():
        matches = sum(1 for p in patterns if re.search(p, text_lower))
        match_counts[doc_type] = matches
        scores[doc_type] = matches / len(patterns) if patterns else 0

    if not scores:
        return ClassificationResult(
            document_type="generic",
            confidence=0.3,
            method="heuristic",
        )

    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]
    best_matches = match_counts.get(best_type, 0)

    # Retail tax invoices often say "Tax Invoice/Bill of Supply" and contain
    # order/GST identifiers instead of classic "amount due" wording.
    if best_type == "invoice" and best_matches >= 3:
        return ClassificationResult(
            document_type="invoice",
            confidence=min(0.9, 0.45 + best_matches * 0.08),
            method="heuristic",
        )

    # Resumes often omit the literal word "resume"; a couple of strong section
    # signals are enough to identify them.
    if best_type == "resume" and best_matches >= 2:
        return ClassificationResult(
            document_type="resume",
            confidence=min(0.85, 0.45 + best_matches * 0.1),
            method="heuristic",
        )

    # If no type scored above threshold, classify as generic
    if best_score < HEURISTIC_CONFIDENCE_THRESHOLD:
        if best_score > 0.2:
            return ClassificationResult(
                document_type=best_type,
                confidence=best_score,
                method="heuristic",
            )
        return ClassificationResult(
            document_type="generic",
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
    Classify a document using LLM for open-ended, dynamic document classification.
    """
    if not settings.llm_available:
        return ClassificationResult(
            document_type="generic",
            confidence=0.3,
            method="heuristic_fallback",
        )

    truncated_text = text[:3000]
    prompt = (
        "You are an expert document classifier. Analyze the text below and classify it into a specific, "
        "lowercase snake_case document type (e.g. invoice, receipt, contract, resume, bank_statement, "
        "tax_return, medical_report, bill_of_lading, purchase_order, utility_bill, lease_agreement, etc.).\n\n"
        "Return a JSON object with keys:\n"
        "- \"document_type\": string (lowercase snake_case)\n"
        "- \"confidence\": float (0.0 to 1.0)\n\n"
        f"Document snippet:\n{truncated_text}"
    )

    try:
        provider = settings.llm_provider.lower()
        if provider == "openai":
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw_json = response.choices[0].message.content
        elif provider == "gemini":
            import httpx

            url = (
                "https://generativelanguage.googleapis.com/v1beta/"
                f"{settings.gemini_model if settings.gemini_model.startswith('models/') else 'models/' + settings.gemini_model}:generateContent"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                },
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": settings.gemini_api_key,
                    },
                    json=payload,
                )
                resp.raise_for_status()
                raw_json = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return classify_by_heuristic(text)

        data = json.loads(raw_json)
        doc_type = str(data.get("document_type", "generic")).strip().lower().replace(" ", "_")
        conf = float(data.get("confidence", 0.85))
        conf = max(0.0, min(1.0, conf))

        return ClassificationResult(
            document_type=doc_type or "generic",
            confidence=conf,
            method="llm",
        )
    except Exception:
        return classify_by_heuristic(text)


async def classify_document(text: str) -> ClassificationResult:
    """
    Main classification entry point.
    """
    heuristic_result = classify_by_heuristic(text)

    # If heuristic matched a specific type (invoice, receipt, contract, resume) with high confidence, use it
    if (
        heuristic_result.confidence >= HEURISTIC_CONFIDENCE_THRESHOLD
        and heuristic_result.document_type != "generic"
    ):
        return heuristic_result

    # Otherwise escalate to LLM for open-ended, dynamic document type classification
    if settings.llm_available:
        llm_result = await classify_by_llm(text)
        if llm_result.document_type != "generic" or heuristic_result.document_type == "generic":
            return llm_result

    return heuristic_result
