"""
Stage 3: Structured Data Extractor

Extracts structured data from parsed text using LLM with open-ended schema discovery.
"""

import asyncio
import json
import random
from typing import Any

from app.config import settings
from app.utils.rate_limit import throttle_gemini_request

MAX_RETRIES = 5
BASE_BACKOFF = 2.0


async def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter for rate-limit errors."""
    delay = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
    await asyncio.sleep(delay)


async def extract_with_llm(
    text: str,
    document_type: str,
) -> dict[str, Any]:
    """
    Extract structured data using the configured LLM provider.
    Discovers fields dynamically from the document without forcing a hardcoded schema.
    """
    provider = settings.llm_provider.lower()
    from app.utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage

    prompt = _build_extraction_prompt(text, document_type)
    
    # We will use the fast model for extraction to save time, or strong model if configured
    llm = get_llm(model_type="fast", temperature=0.0)

    messages = [
        SystemMessage(content="You are a professional document extractor. Output a comprehensive and valid JSON object."),
        HumanMessage(content=prompt)
    ]
    
    response = None
    for attempt in range(MAX_RETRIES):
        try:
            if provider == "gemini":
                from app.utils.rate_limit import throttle_gemini_request
                await throttle_gemini_request()
            response = await llm.ainvoke(messages)
            break
        except Exception as e:
            if attempt == MAX_RETRIES - 1 or ("429" not in str(e) and "503" not in str(e)):
                raise
            await _sleep_backoff(attempt)

    raw_content = response.content
    if isinstance(raw_content, list):
        raw_content = raw_content[0].get("text", "") if raw_content else ""
    raw_json = str(raw_content).strip()
    if raw_json.startswith("```json"):
        raw_json = raw_json[7:]
    elif raw_json.startswith("```"):
        raw_json = raw_json[3:]
    if raw_json.endswith("```"):
        raw_json = raw_json[:-3]

    return json.loads(raw_json.strip())


def _build_extraction_prompt(text: str, document_type: str) -> str:
    truncated_text = text[:2000000]
    return (
        f"You are an expert document data extractor. Extract ALL relevant structured data from this "
        f"'{document_type}' document into a comprehensive JSON object.\n\n"
        f"Rules:\n"
        f"1. Use snake_case for all JSON field names.\n"
        f"2. Extract as many relevant fields as possible: dates, amounts, names, addresses, line items, taxes, totals, status, terms, identifiers, and any key entities.\n"
        f"3. Preserve nested lists or structures (e.g. line_items, items, work_experience) where appropriate.\n"
        f"4. If a field cannot be determined, do not invent it.\n"
        f"5. Be precise and extract only facts explicitly stated in the document.\n\n"
        f"Return ONLY a single valid JSON object. Do not include markdown code block syntax unless necessary.\n"
        f"Document text:\n{truncated_text}"
    )


async def extract_structured_data(
    text: str,
    document_type: str,
    file_path: str = "",
) -> tuple[dict[str, Any], str]:
    if not settings.llm_available:
        raise ValueError("LLM is required for extraction but is not available or enabled.")
        
    data = await extract_with_llm(text, document_type)
    return data, "llm"
