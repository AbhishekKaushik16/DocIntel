"""
Stage 3: Structured Data Extractor

Extracts structured data from parsed text using either:
1. LLM with structured outputs (primary — when API key is available)
2. Regex-based extraction (fallback — always available)

Each document type has a dedicated Pydantic schema that defines
the expected output structure.
"""

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.models.document import DocumentType


# ══════════════════════════════════════════════════════════════
# Extraction Schemas (per document type)
# ══════════════════════════════════════════════════════════════


class LineItem(BaseModel):
    """A single line item on an invoice or receipt."""
    description: str = ""
    product_code: str | None = None
    hsn_code: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    net_amount: float | None = None
    tax_rate: str | None = None
    tax_type: str | None = None
    tax_amount: float | None = None
    amount: float | None = None


class TaxRegistration(BaseModel):
    """Seller tax and compliance identifiers."""
    pan_number: str | None = None
    gst_registration_number: str | None = None
    fssai_license_number: str | None = None


class InvoiceExtraction(BaseModel):
    """Structured data extracted from an invoice."""
    invoice_number: str | None = None
    order_number: str | None = None
    invoice_details: str | None = None
    invoice_date: str | None = None
    order_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_tax_registration: TaxRegistration | None = None
    bill_to_name: str | None = None
    bill_to_address: str | None = None
    ship_to_name: str | None = None
    ship_to_address: str | None = None
    place_of_supply: str | None = None
    place_of_delivery: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    amount_in_words: str | None = None
    invoice_value: float | None = None
    payment_transaction_id: str | None = None
    payment_datetime: str | None = None
    payment_mode: str | None = None
    reverse_charge_applicable: bool | None = None
    currency: str = "INR"
    payment_terms: str | None = None
    notes: str | None = None


class ReceiptExtraction(BaseModel):
    """Structured data extracted from a receipt."""
    store_name: str | None = None
    store_address: str | None = None
    transaction_date: str | None = None
    transaction_id: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    payment_method: str | None = None
    currency: str = "USD"


class ContractExtraction(BaseModel):
    """Structured data extracted from a contract."""
    contract_title: str | None = None
    parties: list[str] = Field(default_factory=list)
    effective_date: str | None = None
    expiration_date: str | None = None
    contract_value: float | None = None
    key_terms: list[str] = Field(default_factory=list)
    governing_law: str | None = None
    summary: str | None = None


class WorkExperience(BaseModel):
    """A single work experience entry on a resume."""
    company: str = ""
    title: str = ""
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None


class Education(BaseModel):
    """A single education entry on a resume."""
    institution: str = ""
    degree: str = ""
    field_of_study: str | None = None
    graduation_date: str | None = None


class ResumeExtraction(BaseModel):
    """Structured data extracted from a resume/CV."""
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    linkedin_url: str | None = None


class GenericExtraction(BaseModel):
    """Fallback extraction for unclassified documents."""
    title: str | None = None
    author: str | None = None
    date: str | None = None
    summary: str | None = None
    key_entities: list[str] = Field(default_factory=list)
    amounts: list[float] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


# Schema map
EXTRACTION_SCHEMAS: dict[DocumentType, type[BaseModel]] = {
    DocumentType.INVOICE: InvoiceExtraction,
    DocumentType.RECEIPT: ReceiptExtraction,
    DocumentType.CONTRACT: ContractExtraction,
    DocumentType.RESUME: ResumeExtraction,
    DocumentType.GENERIC: GenericExtraction,
}


# ══════════════════════════════════════════════════════════════
# LLM Extraction
# ══════════════════════════════════════════════════════════════


async def extract_with_llm(
    text: str,
    document_type: DocumentType,
) -> dict[str, Any]:
    """
    Extract structured data using the configured LLM provider.

    Uses the document type's Pydantic schema to enforce output structure.
    """
    provider = settings.llm_provider.lower()
    if provider == "openai":
        return await extract_with_openai(text, document_type)
    if provider == "gemini":
        return await extract_with_gemini(text, document_type)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def _build_extraction_prompt(text: str, document_type: DocumentType) -> str:
    schema_class = EXTRACTION_SCHEMAS[document_type]
    schema = _json_schema_for_provider(schema_class)
    truncated_text = text[:8000]
    return (
        f"You are a document data extractor. Extract structured data from the following "
        f"{document_type.value} document. Return only a JSON object matching this schema. "
        f"If a field cannot be determined from the text, use null. "
        f"Be precise and extract only what is explicitly stated.\n\n"
        f"JSON schema:\n{json.dumps(schema, indent=2)}\n\n"
        f"Document text:\n{truncated_text}"
    )


def _json_schema_for_provider(schema_class: type[BaseModel]) -> dict[str, Any]:
    """Return JSON Schema without Pydantic keywords Gemini rejects."""
    return _strip_json_schema_keywords(schema_class.model_json_schema())


def _gemini_model_path(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


def _strip_json_schema_keywords(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_json_schema_keywords(item)
            for key, item in value.items()
            if key not in {"default"}
        }
    if isinstance(value, list):
        return [_strip_json_schema_keywords(item) for item in value]
    return value


def _validate_llm_json(raw_json: str, document_type: DocumentType) -> dict[str, Any]:
    schema_class = EXTRACTION_SCHEMAS[document_type]
    parsed = json.loads(raw_json)

    # Validate against schema (lenient — don't crash on unexpected fields)
    try:
        validated = schema_class.model_validate(parsed)
        return validated.model_dump()
    except Exception:
        # If validation fails, return raw parsed JSON
        return parsed


async def extract_with_openai(
    text: str,
    document_type: DocumentType,
) -> dict[str, Any]:
    """Extract structured data using OpenAI's JSON response mode."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    schema_class = EXTRACTION_SCHEMAS[document_type]

    # Truncate to ~8000 chars to stay within token limits while keeping cost low
    truncated_text = text[:8000]

    system_prompt = (
        f"You are a document data extractor. Extract structured data from the following "
        f"{document_type.value} document. Return a JSON object matching the schema exactly. "
        f"If a field cannot be determined from the text, use null. "
        f"Be precise — extract only what is explicitly stated in the document."
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": truncated_text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw_json = response.choices[0].message.content
    return _validate_llm_json(raw_json, document_type)


async def extract_with_gemini(
    text: str,
    document_type: DocumentType,
) -> dict[str, Any]:
    """Extract structured data using Gemini's Generate Content REST API."""
    schema_class = EXTRACTION_SCHEMAS[document_type]
    schema = _json_schema_for_provider(schema_class)
    prompt = _build_extraction_prompt(text, document_type)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"{_gemini_model_path(settings.gemini_model)}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key,
            },
            json=payload,
        )
        response.raise_for_status()

    body = response.json()
    try:
        raw_json = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Gemini response did not include JSON text") from exc

    return _validate_llm_json(raw_json, document_type)


# ══════════════════════════════════════════════════════════════
# Regex Fallback Extraction
# ══════════════════════════════════════════════════════════════

# Common patterns
DATE_PATTERN = re.compile(
    r"\b(\d{2,4}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{2,4}\b)",
    re.IGNORECASE,
)
CURRENCY_PATTERN = re.compile(
    r"[\$€£¥]\s*[\d,]+\.?\d*"
    r"|\d[\d,]*\.\d{2}\s*(?:USD|EUR|GBP|INR|CAD)",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(
    r"\+?\d{1,3}[\s\-\.]?\(?\d{2,4}\)?[\s\-\.]?\d{3,4}[\s\-\.]?\d{3,4}"
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
INVOICE_NUM_PATTERN = re.compile(r"(?:invoice|inv|bill)[\s#\-.:]*([A-Z0-9\-]+)", re.IGNORECASE)


def extract_with_regex(text: str, document_type: DocumentType) -> dict[str, Any]:
    """
    Extract data using regex patterns. This is the fallback when no LLM is available.

    Not as accurate as LLM extraction, but catches common patterns reliably.
    """
    dates = [m.group() for m in DATE_PATTERN.finditer(text)]
    amounts = CURRENCY_PATTERN.findall(text)
    emails = EMAIL_PATTERN.findall(text)
    phones = PHONE_PATTERN.findall(text)
    urls = URL_PATTERN.findall(text)

    if document_type == DocumentType.INVOICE:
        inv_match = INVOICE_NUM_PATTERN.search(text)
        total_match = re.search(r"(?:total\s+amount|total\s+due|\btotal\b)[\s:]*([\$€£¥]?\s*[\d,]+\.?\d*)", text, re.IGNORECASE)
        subtotal_match = re.search(r"(?:subtotal|sub-total)[\s:]*([\$€£¥]?\s*[\d,]+\.?\d*)", text, re.IGNORECASE)
        
        tot_val = _parse_currency(total_match.group(1)) if total_match else (_parse_currency(amounts[-1]) if amounts else None)
        sub_val = _parse_currency(subtotal_match.group(1)) if subtotal_match else (_parse_currency(amounts[-2]) if len(amounts) >= 2 else None)

        return InvoiceExtraction(
            invoice_number=inv_match.group(1) if inv_match else None,
            invoice_date=dates[0] if dates else None,
            due_date=dates[1] if len(dates) > 1 else None,
            total_amount=tot_val,
            subtotal=sub_val,
        ).model_dump()

    elif document_type == DocumentType.RECEIPT:
        return ReceiptExtraction(
            transaction_date=dates[0] if dates else None,
            total_amount=_parse_currency(amounts[-1]) if amounts else None,
        ).model_dump()

    elif document_type == DocumentType.RESUME:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return ResumeExtraction(
            full_name=_extract_resume_name(lines),
            email=emails[0] if emails else None,
            phone=phones[0] if phones else None,
            location=_extract_resume_location(lines),
            skills=_extract_resume_skills(text),
            work_experience=_extract_resume_work_experience(lines),
            linkedin_url=_extract_linkedin(text, urls),
        ).model_dump()

    elif document_type == DocumentType.CONTRACT:
        return ContractExtraction(
            effective_date=dates[0] if dates else None,
            contract_value=_parse_currency(amounts[0]) if amounts else None,
        ).model_dump()

    # Generic fallback
    return GenericExtraction(
        date=dates[0] if dates else None,
        amounts=[_parse_currency(a) for a in amounts if _parse_currency(a) is not None],
        emails=emails,
        phone_numbers=phones,
        urls=urls,
    ).model_dump()


def _parse_currency(value: str) -> float | None:
    """Parse a currency string into a float."""
    if not value:
        return None
    try:
        # Remove currency symbols and commas
        cleaned = re.sub(r"[^\d.]", "", value)
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def _extract_resume_name(lines: list[str]) -> str | None:
    """Infer a resume name from the first content line."""
    for line in lines[:5]:
        if line.startswith("--- Page"):
            continue
        if EMAIL_PATTERN.search(line) or PHONE_PATTERN.search(line):
            continue
        if len(line.split()) <= 4 and not any(char.isdigit() for char in line):
            return line
    return None


def _extract_resume_location(lines: list[str]) -> str | None:
    for line in lines[:8]:
        if "," in line and not EMAIL_PATTERN.search(line) and not PHONE_PATTERN.search(line):
            return line
    return None


def _extract_resume_skills(text: str) -> list[str]:
    skills: list[str] = []
    section_match = re.search(
        r"(?:technical\s+skills|skills)\s*(.*?)(?:\n\s*(?:work\s+experience|experience|projects|education)\b|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return skills

    for line in section_match.group(1).splitlines():
        if ":" in line:
            line = line.split(":", 1)[1]
        for item in re.split(r"[,|]", line):
            cleaned = re.sub(r"[^\x20-\x7E]", "", item).strip(" \t.;")
            if cleaned and len(cleaned) <= 40:
                skills.append(cleaned)

    return list(dict.fromkeys(skills))


def _extract_resume_work_experience(lines: list[str]) -> list[WorkExperience]:
    experiences: list[WorkExperience] = []
    date_range = re.compile(
        r"\b(?:\d{2}/\d{4}|[A-Z][a-z]{2,8}\s+\d{4})\s*[-–]\s*(?:Current|Present|\d{2}/\d{4}|[A-Z][a-z]{2,8}\s+\d{4})\b",
        re.IGNORECASE,
    )

    in_work = False
    for index, line in enumerate(lines):
        lower = line.lower()
        if "work experience" in lower or lower == "experience":
            in_work = True
            continue
        if in_work and lower in {"education", "projects", "technical skills", "skills"}:
            break
        if not in_work:
            continue

        match = date_range.search(line)
        if not match:
            continue

        company = lines[index - 1] if index >= 1 else ""
        title = lines[index + 1] if index + 1 < len(lines) else ""
        start_date, end_date = re.split(r"\s*[-–]\s*", match.group(), maxsplit=1)
        if company and title:
            experiences.append(WorkExperience(
                company=company,
                title=title,
                start_date=start_date,
                end_date=end_date,
            ))

    return experiences


def _extract_linkedin(text: str, urls: list[str]) -> str | None:
    url = next((u for u in urls if "linkedin" in u.lower()), None)
    if url:
        return url
    match = re.search(r"(?:linkedin(?:\.com)?/in/|linkedin/)([A-Za-z0-9\-_.]+)", text, re.IGNORECASE)
    if match:
        return f"https://linkedin.com/in/{match.group(1)}"
    return None


# ══════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════


async def extract_structured_data(
    text: str,
    document_type: DocumentType,
) -> tuple[dict[str, Any], str]:
    """
    Main extraction entry point.

    Returns (extracted_data, method) where method is 'llm' or 'regex'.
    """
    if settings.llm_available:
        try:
            data = await extract_with_llm(text, document_type)
            return data, "llm"
        except Exception as e:
            # LLM failed — fall back to regex
            data = extract_with_regex(text, document_type)
            return data, f"regex_fallback (llm error: {str(e)[:100]})"

    # No LLM available — use regex
    data = extract_with_regex(text, document_type)
    return data, "regex"
