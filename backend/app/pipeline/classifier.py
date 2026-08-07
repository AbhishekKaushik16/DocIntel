"""
Classifier Agent

An iterative, tool-using agent that determines document type.

Instead of a heuristic pass → single LLM fallback, the agent has three tools it can
invoke in any order before committing to a type:

  • check_file_metadata   — file extension, size, MIME hint
  • sample_page_text      — extract a text snippet from a specific page
  • run_ocr_preview       — run OCR on a small thumbnail of a page

The agent may call multiple tools before returning a final type. For example:
  1. call check_file_metadata → sees it's a PDF
  2. call sample_page_text(page=1) → text looks sparse
  3. call run_ocr_preview(page=1) → confirms it's a scanned invoice
  4. → commits to "invoice" with reasoning

Tool loop is capped at MAX_TOOL_ROUNDS to prevent infinite looping.
"""

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.utils.rate_limit import throttle_gemini_request


MAX_TOOL_ROUNDS = 5   # Maximum agent iterations before forcing a decision
MAX_RETRIES = 1       # Retries on 429 / 503 per LLM call
BASE_BACKOFF = 1.0    # Seconds — doubles each retry with jitter


async def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter for rate-limit errors."""
    delay = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
    await asyncio.sleep(delay)


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Result of the Classifier Agent."""
    document_type: str
    confidence: float
    method: str          # "agent_tools", "agent_direct", "heuristic_fallback"
    reasoning: str       # Agent's chain-of-thought explaining the decision
    agent_steps: list[dict[str, Any]] = field(default_factory=list)


# ── Tool implementations ───────────────────────────────────────────────────────

def _tool_check_file_metadata(file_path: str) -> dict[str, Any]:
    """
    Tool: check_file_metadata
    Returns file extension, size in bytes, and any MIME clues available from
    the filename. Cheap and always succeeds.
    """
    p = Path(file_path)
    ext = p.suffix.lower()
    size = p.stat().st_size if p.exists() else 0
    ext_hints = {
        ".pdf": "PDF document — may be text-based or scanned",
        ".png": "PNG image — likely scanned or screenshot",
        ".jpg": "JPEG image — likely scanned or screenshot",
        ".jpeg": "JPEG image — likely scanned or screenshot",
        ".tiff": "TIFF image — often used for high-quality scans",
        ".docx": "Word document — text-based",
        ".xlsx": "Excel spreadsheet — tabular data",
        ".csv": "CSV file — tabular data",
        ".txt": "Plain text file",
    }
    return {
        "filename": p.name,
        "extension": ext,
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
        "format_hint": ext_hints.get(ext, f"Unknown format ({ext})"),
    }


def _tool_sample_page_text(file_path: str, page_num: int = 1) -> dict[str, Any]:
    """
    Tool: sample_page_text
    Extracts up to 800 characters of text from the given page number (1-indexed).
    Uses direct PDF text extraction, no OCR. Returns empty string if page has no
    extractable text (indicating a scanned/image page).
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(file_path)
            page_idx = max(0, page_num - 1)
            if page_idx >= len(doc):
                return {"page": page_num, "text_snippet": "", "char_count": 0,
                        "note": f"Document only has {len(doc)} pages."}
            page = doc[page_idx]
            text = page.get_text("text").strip()
            doc.close()
            return {
                "page": page_num,
                "text_snippet": text[:800],
                "char_count": len(text),
                "has_extractable_text": len(text) > 30,
            }
        except Exception as e:
            return {"page": page_num, "text_snippet": "", "char_count": 0, "error": str(e)}

    elif ext in {".xlsx", ".csv"}:
        try:
            import pandas as pd
            if ext == ".xlsx":
                df = pd.read_excel(file_path, nrows=5)
            else:
                df = pd.read_csv(file_path, nrows=5)
            snippet = df.to_string(max_rows=5, max_cols=10)
            return {"page": 1, "text_snippet": snippet[:800], "char_count": len(snippet), "has_extractable_text": True}
        except Exception as e:
            return {"page": 1, "text_snippet": "", "char_count": 0, "error": str(e)}

    elif ext in {".docx"}:
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(file_path)
            text = "\n".join(p.text for p in doc.paragraphs[:10] if p.text.strip())
            return {"page": 1, "text_snippet": text[:800], "char_count": len(text), "has_extractable_text": True}
        except Exception as e:
            return {"page": 1, "text_snippet": "", "char_count": 0, "error": str(e)}

    elif ext in {".txt", ".md"}:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(800)
            return {"page": 1, "text_snippet": text, "char_count": len(text), "has_extractable_text": True}
        except Exception as e:
            return {"page": 1, "text_snippet": "", "char_count": 0, "error": str(e)}

    return {"page": page_num, "text_snippet": "", "char_count": 0,
            "note": f"No direct text reader for {ext}"}


def _tool_run_ocr_preview(file_path: str, page_num: int = 1) -> dict[str, Any]:
    """
    Tool: run_ocr_preview
    Renders a small (150 DPI) thumbnail of the page and runs Tesseract OCR.
    Returns a text snippet, character count, and an estimated OCR confidence.
    Use this when sample_page_text returns empty (page is image-based).
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        try:
            import fitz
            from PIL import Image
            import pytesseract
            import io

            doc = fitz.open(file_path)
            page_idx = max(0, page_num - 1)
            if page_idx >= len(doc):
                return {"page": page_num, "ocr_text": "", "char_count": 0,
                        "note": f"Document only has {len(doc)} pages."}
            # 150 DPI is enough for a quick preview — 300 DPI is used in full pipeline
            pix = doc[page_idx].get_pixmap(dpi=150)
            doc.close()

            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img = img.convert("L")  # grayscale for speed

            # Get confidence data
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confs = [c for c in data["conf"] if c != -1]
            avg_conf = round(sum(confs) / len(confs), 1) if confs else 0.0

            text = pytesseract.image_to_string(img).strip()
            return {
                "page": page_num,
                "ocr_text": text[:600],
                "char_count": len(text),
                "avg_ocr_confidence": avg_conf,   # 0–100; < 50 means poor scan quality
                "is_readable": avg_conf >= 50 and len(text) > 20,
            }
        except Exception as e:
            return {"page": page_num, "ocr_text": "", "char_count": 0, "error": str(e)}

    elif ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:
        try:
            from PIL import Image
            import pytesseract

            img = Image.open(file_path).convert("L")
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            confs = [c for c in data["conf"] if c != -1]
            avg_conf = round(sum(confs) / len(confs), 1) if confs else 0.0

            text = pytesseract.image_to_string(img).strip()
            return {
                "page": 1,
                "ocr_text": text[:600],
                "char_count": len(text),
                "avg_ocr_confidence": avg_conf,
                "is_readable": avg_conf >= 50 and len(text) > 20,
            }
        except Exception as e:
            return {"page": 1, "ocr_text": "", "char_count": 0, "error": str(e)}

    return {"page": page_num, "ocr_text": "", "char_count": 0,
            "note": f"OCR preview not applicable for {ext}"}


# ── Tool dispatch ──────────────────────────────────────────────────────────────

TOOL_SPECS = [
    {
        "name": "check_file_metadata",
        "description": (
            "Returns file extension, size, and format hints. "
            "Always call this first to understand what kind of file you are classifying."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the document file."}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "sample_page_text",
        "description": (
            "Extracts up to 800 chars of direct text from a specific page (PDF, DOCX, XLSX, etc). "
            "Returns empty string if the page has no extractable text (scanned/image page). "
            "Use this before running OCR to check if the document has native text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file."},
                "page_num": {"type": "integer", "description": "1-indexed page number.", "default": 1},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "run_ocr_preview",
        "description": (
            "Runs lightweight OCR (150 DPI) on a page and returns a text snippet, "
            "character count, and avg_ocr_confidence (0-100). "
            "Use this when sample_page_text returns empty text to check if the page "
            "is a low-quality scan or a blank page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file."},
                "page_num": {"type": "integer", "description": "1-indexed page number.", "default": 1},
            },
            "required": ["file_path"],
        },
    },
]

OPENAI_TOOL_SPECS = [
    {"type": "function", "function": spec} for spec in TOOL_SPECS
]

GEMINI_TOOL_SPECS = {
    "function_declarations": TOOL_SPECS
}


def _dispatch_tool(name: str, args: dict) -> Any:
    """Execute a tool by name and return its result."""
    fp = args.get("file_path", "")
    page = int(args.get("page_num", 1))

    if name == "check_file_metadata":
        return _tool_check_file_metadata(fp)
    if name == "sample_page_text":
        return _tool_sample_page_text(fp, page)
    if name == "run_ocr_preview":
        return _tool_run_ocr_preview(fp, page)
    return {"error": f"Unknown tool: {name}"}


# ── Agent loop implementations ─────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an expert document classifier agent. You have three tools to help you "
    "determine the document type before committing to a final answer.\n\n"
    "Strategy:\n"
    "1. Always start with check_file_metadata to understand the file format.\n"
    "2. Use sample_page_text on the first page to get native text if available.\n"
    "3. If sample_page_text returns empty text, use run_ocr_preview to check if "
    "   the page is a readable scan or just blank.\n"
    "4. Use your observations to reason about the document type iteratively.\n"
    "5. Once you are confident, return a final JSON object (no markdown):\n"
    "   {\"document_type\": \"<lowercase_snake_case>\", \"confidence\": <0.0-1.0>, "
    "   \"reasoning\": \"<one or two sentences explaining your decision>\"}\n\n"
    "Document types include: invoice, receipt, contract, resume, bank_statement, "
    "tax_return, medical_report, purchase_order, utility_bill, study_guide, "
    "interview_guide, or any other descriptive snake_case type.\n\n"
    "Do not guess without using at least one tool."
)


async def _run_openai_agent(file_path: str) -> ClassificationResult:
    """Run the Classifier Agent using OpenAI's native tool-calling API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Please classify this document: {file_path}"},
    ]
    agent_steps: list[dict] = []

    for round_num in range(MAX_TOOL_ROUNDS):
        response = await client.chat.completions.create(
            model=settings.fast_model_name,
            messages=messages,
            tools=OPENAI_TOOL_SPECS,
            tool_choice="auto",
            temperature=0,
        )
        msg = response.choices[0].message

        # Agent wants to call tools
        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = _dispatch_tool(tc.function.name, args)
                agent_steps.append({
                    "round": round_num + 1,
                    "tool": tc.function.name,
                    "input": args,
                    "output": result,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        # Agent is done — parse the final JSON answer
        elif msg.content:
            try:
                raw = msg.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())
                return ClassificationResult(
                    document_type=str(data.get("document_type", "generic")).strip().lower().replace(" ", "_"),
                    confidence=max(0.0, min(1.0, float(data.get("confidence", 0.85)))),
                    method="agent_tools" if agent_steps else "agent_direct",
                    reasoning=str(data.get("reasoning", "")),
                    agent_steps=agent_steps,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                # Model returned free-text instead of JSON — extract type from text
                text_lower = msg.content.lower()
                for doc_type in ["invoice", "receipt", "contract", "resume", "bank_statement"]:
                    if doc_type in text_lower:
                        return ClassificationResult(
                            document_type=doc_type,
                            confidence=0.7,
                            method="agent_text_parse",
                            reasoning=msg.content[:300],
                            agent_steps=agent_steps,
                        )
                break

    return ClassificationResult(
        document_type="generic",
        confidence=0.3,
        method="agent_max_rounds",
        reasoning=f"Agent did not converge after {MAX_TOOL_ROUNDS} rounds.",
        agent_steps=agent_steps,
    )


async def _run_gemini_agent(file_path: str) -> ClassificationResult:
    """Run the Classifier Agent using LangChain with Gemini."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
    
    llm = ChatGoogleGenerativeAI(
        model=settings.fast_model_name,
        google_api_key=settings.gemini_api_key,
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(TOOL_SPECS)
    
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Please classify this document: {file_path}")
    ]
    agent_steps: list[dict] = []

    for round_num in range(MAX_TOOL_ROUNDS):
        response = None
        for attempt in range(MAX_RETRIES):
            try:
                await throttle_gemini_request()
                response = await llm_with_tools.ainvoke(messages)
                break
            except Exception as e:
                if attempt == MAX_RETRIES - 1 or ("429" not in str(e) and "503" not in str(e)):
                    raise
                await _sleep_backoff(attempt)
        
        if response.tool_calls:
            messages.append(response)
            for tool_call in response.tool_calls:
                result = _dispatch_tool(tool_call["name"], tool_call["args"])
                agent_steps.append({
                    "round": round_num + 1,
                    "tool": tool_call["name"],
                    "input": tool_call["args"],
                    "output": result,
                })
                messages.append(ToolMessage(
                    tool_call_id=tool_call["id"],
                    content=json.dumps(result)
                ))
        else:
            # Agent is done — parse JSON answer
            try:
                raw_content = response.content
                if isinstance(raw_content, list):
                    raw_content = raw_content[0].get("text", "") if raw_content else ""
                raw = str(raw_content).strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())
                return ClassificationResult(
                    document_type=str(data.get("document_type", "generic")).strip().lower().replace(" ", "_"),
                    confidence=max(0.0, min(1.0, float(data.get("confidence", 0.85)))),
                    method="agent_tools" if agent_steps else "agent_direct",
                    reasoning=str(data.get("reasoning", "")),
                    agent_steps=agent_steps,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                text_lower = str(raw_content).lower()
                for doc_type in ["invoice", "receipt", "contract", "resume", "bank_statement"]:
                    if doc_type in text_lower:
                        return ClassificationResult(
                            document_type=doc_type,
                            confidence=0.7,
                            method="agent_text_parse",
                            reasoning=str(raw_content)[:300],
                            agent_steps=agent_steps,
                        )
                break

    return ClassificationResult(
        document_type="generic",
        confidence=0.3,
        method="agent_max_rounds",
        reasoning=f"Gemini agent did not converge after {MAX_TOOL_ROUNDS} rounds.",
        agent_steps=agent_steps,
    )


# ── Heuristic fallback (no LLM) ───────────────────────────────────────────────

import re

KEYWORD_PATTERNS: dict[str, list[str]] = {
    "invoice": [
        r"\binvoice\b", r"\binv[\s#\-.:]*\d+", r"\bbill\s+to\b",
        r"\btotal\s+due\b", r"\bamount\s+due\b", r"\bpayment\s+terms\b",
        r"\bdue\s+date\b", r"\bsubtotal\b", r"\btax\b.*\bamount\b",
        r"\bpurchase\s+order\b", r"\bpo[\s#\-.:]*\d+",
        r"\btax\s+invoice\b", r"\bbill\s+of\s+supply\b",
    ],
    "receipt": [
        r"\breceipt\b", r"\btransaction\b", r"\bpaid\b",
        r"\bchange\s+due\b", r"\bcard\s+ending\b", r"\btotal\s*[:$]",
    ],
    "contract": [
        r"\bagreement\b", r"\bcontract\b", r"\bterms\s+and\s+conditions\b",
        r"\bhereby\b", r"\bwhereas\b", r"\beffective\s+date\b",
        r"\btermination\b", r"\bclause\b", r"\bexecuted\b",
    ],
    "resume": [
        r"\bresume\b", r"\bcurriculum\s+vitae\b",
        r"\bwork\s+experience\b", r"\bskills\b",
        r"\bemployment\s+history\b", r"\blinkedin\.com\b",
    ],
}


def _heuristic_fallback(file_path: str) -> ClassificationResult:
    """Quick keyword-based fallback when LLM is unavailable."""
    meta = _tool_check_file_metadata(file_path)
    text_sample = _tool_sample_page_text(file_path, 1).get("text_snippet", "")
    text_lower = text_sample.lower()

    scores: dict[str, float] = {}
    for doc_type, patterns in KEYWORD_PATTERNS.items():
        matches = sum(1 for p in patterns if re.search(p, text_lower))
        scores[doc_type] = matches / len(patterns) if patterns else 0

    if scores:
        best = max(scores, key=scores.__getitem__)
        if scores[best] >= 0.20:
            return ClassificationResult(
                document_type=best,
                confidence=min(0.80, scores[best] + 0.30),
                method="heuristic",
                reasoning=f"Matched keyword patterns for '{best}' (score={scores[best]:.2f}). No LLM available.",
            )

    # Extension-based last resort
    ext_map = {".xlsx": "spreadsheet", ".csv": "spreadsheet", ".docx": "document", ".pdf": "document"}
    doc_type = ext_map.get(meta["extension"], "generic")
    return ClassificationResult(
        document_type=doc_type,
        confidence=0.30,
        method="heuristic_extension",
        reasoning=f"No keyword matches; fell back to extension-based type: {doc_type}",
    )


# ── Public entry point ─────────────────────────────────────────────────────────

async def classify_document(file_path: str) -> ClassificationResult:
    """
    Main classification entry point.

    Strategy (cheapest-first):
      1. Run local heuristic (zero API calls: metadata + text sample + keywords).
         If confidence >= 0.75, return immediately — no LLM needed.
      2. Otherwise invoke the LLM agent with tool-calling for uncertain/novel types.
      3. On any LLM error (including 429 rate-limit), fall back to heuristic.

    Args:
        file_path: Absolute path to the document on disk.
    Returns:
        ClassificationResult with document_type, confidence, reasoning, agent_steps.
    """
    # ── Step 1: Local heuristic pre-flight (free, instant) ────────────────────
    heuristic = _heuristic_fallback(file_path)
    if heuristic.confidence >= 0.75:
        # Confident heuristic match — no LLM call needed
        return ClassificationResult(
            document_type=heuristic.document_type,
            confidence=heuristic.confidence,
            method="heuristic_confident",
            reasoning=(
                f"High-confidence heuristic match: '{heuristic.document_type}' "
                f"(confidence={heuristic.confidence:.2f}). LLM skipped."
            ),
            agent_steps=[],
        )

    # ── Step 2: LLM agent for uncertain / novel document types ────────────────
    if not settings.llm_available:
        return heuristic  # No LLM configured — use heuristic as-is

    try:
        provider = settings.llm_provider.lower()
        if provider == "openai":
            result = await _run_openai_agent(file_path)
        elif provider == "gemini":
            result = await _run_gemini_agent(file_path)
        else:
            result = heuristic

        # If the agent itself failed and returned generic with low confidence,
        # prefer the heuristic result if it's more specific
        if (
            result.document_type == "generic"
            and result.confidence <= 0.35
            and heuristic.document_type != "generic"
        ):
            return ClassificationResult(
                document_type=heuristic.document_type,
                confidence=heuristic.confidence,
                method="heuristic_agent_fallback",
                reasoning=(
                    f"Agent returned generic/low-confidence result "
                    f"({result.reasoning[:80]}). Using heuristic: '{heuristic.document_type}'."
                ),
                agent_steps=result.agent_steps,
            )
        return result

    except Exception as e:
        # 429 rate-limit, network error, etc. — fall back to heuristic gracefully
        return ClassificationResult(
            document_type=heuristic.document_type,
            confidence=heuristic.confidence,
            method="heuristic_rate_limit_fallback",
            reasoning=(
                f"LLM agent unavailable ({str(e)[:120]}). "
                f"Using heuristic result: '{heuristic.document_type}' "
                f"(confidence={heuristic.confidence:.2f})."
            ),
        )

