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


def deep_merge_json(base: dict, update: dict) -> dict:
    """Deep merge two JSON dictionaries."""
    result = base.copy()
    for key, val in update.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = deep_merge_json(result[key], val)
            elif isinstance(result[key], list) and isinstance(val, list):
                # Extend the list with new items avoiding exact duplicates
                for item in val:
                    if item not in result[key]:
                        result[key].append(item)
            elif isinstance(result[key], list) and not isinstance(val, list):
                if val not in result[key]:
                    result[key].append(val)
            elif not isinstance(result[key], list) and isinstance(val, list):
                res_list = [result[key]]
                for item in val:
                    if item not in res_list:
                        res_list.append(item)
                result[key] = res_list
            else:
                # Both are scalars, keep the base one (first encountered)
                pass
        else:
            result[key] = val
    return result


def _build_extraction_prompt(text: str, document_type: str) -> str:
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
        f"Document text:\n{text}"
    )


async def extract_structured_data(
    text: str,
    document_type: str,
    file_path: str = "",
) -> tuple[dict[str, Any], str]:
    if not settings.llm_available:
        raise ValueError("LLM is required for extraction but is not available or enabled.")
        
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    
    # 300k chars is approx 75k tokens, safe for 128k context windows
    splitter = RecursiveCharacterTextSplitter(chunk_size=300000, chunk_overlap=10000)
    chunks = splitter.split_text(text)
    
    if not chunks:
        return {}, "llm"
        
    merged_data = {}
    for i, chunk in enumerate(chunks):
        try:
            chunk_data = await extract_with_llm(chunk, document_type)
            if i == 0:
                merged_data = chunk_data
            else:
                merged_data = deep_merge_json(merged_data, chunk_data)
        except Exception as e:
            import logging
            logging.warning(f"Failed to extract from chunk {i}: {e}")
            if i == 0 and len(chunks) == 1:
                raise
            # If a later chunk fails, we just continue with what we have
    
    method = "llm" if len(chunks) == 1 else f"llm (map-reduce over {len(chunks)} chunks)"
    return merged_data, method
