"""
Resolver Agent

The highest-leverage addition: sits between Validate and a final routing decision.

When cross-field validation fails (total ≠ subtotal + tax, missing required fields,
OCR-induced digit errors), instead of immediately routing to NEEDS_REVIEW, this agent:

  1. Receives the failed validation issues and the original document text.
  2. Uses tools to re-examine just the problematic fields:
     • re_extract_section  — re-prompts the LLM, focused narrowly on broken fields
     • evaluate_math       — sanity-checks arithmetic without LLM hallucination
     • lookup_field        — asks the LLM to find a specific field directly in the text
  3. Returns corrected extracted_data and a reasoning string explaining what it fixed.

If it cannot resolve the issues within MAX_RESOLVER_ROUNDS, it returns the original
data unchanged and sets resolved=False, letting the orchestrator escalate.

Model choice: uses settings.strong_model_name (separate from fast classifier model)
to maximise reasoning quality on this low-volume, high-stakes stage.
"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.utils.rate_limit import throttle_gemini_request


MAX_RESOLVER_ROUNDS = 4   # Maximum tool-call rounds before giving up
MAX_RETRIES = 1
BASE_BACKOFF = 1.0


async def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter for rate-limit errors."""
    delay = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
    await asyncio.sleep(delay)



# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ResolverResult:
    """Result of the Resolver Agent."""
    resolved: bool                              # True if agent fixed at least one issue
    extracted_data: dict[str, Any]              # Updated (or unchanged) extracted data
    reasoning: str                              # Agent's explanation of what it did
    agent_steps: list[dict[str, Any]] = field(default_factory=list)


# ── Tool implementations ───────────────────────────────────────────────────────

async def _tool_re_extract_section(
    raw_text: str,
    document_type: str,
    focus_fields: list[str],
    instruction: str,
) -> dict[str, Any]:
    """
    Tool: re_extract_section
    Re-runs field extraction with a targeted prompt focused only on the
    problematic fields. Uses the strong model for maximum accuracy.
    Returns a partial dict of extracted values for the focus fields.
    """
    fields_str = ", ".join(focus_fields)
    prompt = (
        f"You are a precise document data extractor. The document type is '{document_type}'.\n\n"
        f"Re-examine this document text and extract ONLY these fields: {fields_str}.\n"
        f"Additional instruction: {instruction}\n\n"
        f"Return a JSON object with only the requested fields. Use null for fields you cannot find.\n"
        f"Be precise — extract literal values from the text, do not compute or estimate.\n\n"
        f"Document text:\n{raw_text[:6000]}"
    )

    provider = settings.llm_provider.lower()

    try:
            from app.utils.llm import get_llm
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = get_llm(model_type="strong", temperature=0.0)
            
            response = None
            for attempt in range(MAX_RETRIES):
                try:
                    await throttle_gemini_request()
                    response = await llm.ainvoke([SystemMessage(content="You are a professional document resolving AI. Extract accurate JSON."), HumanMessage(content=prompt)])
                    break
                except Exception as e:
                    if attempt == MAX_RETRIES - 1 or ("429" not in str(e) and "503" not in str(e)):
                        raise
                    await _sleep_backoff(attempt)
            raw_content = response.content
            if isinstance(raw_content, list):
                raw_content = raw_content[0].get("text", "") if raw_content else ""
            raw = str(raw_content).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as e:
            return {"error": str(e)}

    return {"error": "No LLM provider configured"}


def _tool_evaluate_math(expression: str) -> dict[str, Any]:
    """
    Tool: evaluate_math
    Safely evaluates a basic arithmetic expression (addition, subtraction,
    multiplication, division). Used by the resolver to verify totals without
    risking LLM hallucination on number arithmetic.

    Example: evaluate_math("1234.56 + 98.44") → {"result": 1333.0, "expression": "..."}
    """
    import ast
    import operator as op

    allowed_ops = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.UAdd: op.pos,
        ast.USub: op.neg,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsafe expression node: {type(node).__name__}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        return {"expression": expression, "result": round(result, 6)}
    except Exception as e:
        return {"expression": expression, "error": str(e)}


def _tool_lookup_field(raw_text: str, field_name: str, context_hint: str = "") -> dict[str, Any]:
    """
    Tool: lookup_field
    Searches for a specific field value using regex patterns.
    This is a fast, deterministic alternative to a full LLM re-extract for
    simple cases like finding a missing invoice_number or due_date.
    """
    import re

    patterns: dict[str, list[str]] = {
        "invoice_number": [
            r"(?:invoice|inv|bill)[^\n#]*[#\-. :]?\s*([A-Z0-9\-/]+)",
            r"(?:order\s+(?:id|no|number))[^\n:]*:\s*([A-Z0-9\-/]+)",
        ],
        "due_date": [
            r"(?:due\s+date|payment\s+due|pay\s+by)[^\n:]*:\s*([\d/\-\.]+(?:\s+\w+)?)",
        ],
        "invoice_date": [
            r"(?:invoice\s+date|date\s+of\s+invoice|issued)[^\n:]*:\s*([\d/\-\.]+(?:\s+\w+)?)",
        ],
        "total_amount": [
            r"(?:total\s+(?:amount|due|payable)|grand\s+total)[^\n:$]*[:\$]?\s*([\d,]+\.?\d*)",
        ],
        "subtotal": [
            r"(?:subtotal|sub-total)[^\n:$]*[:\$]?\s*([\d,]+\.?\d*)",
        ],
        "tax_amount": [
            r"(?:tax|gst|vat|igst|cgst|sgst)[^\n:$%]*[:\$]?\s*([\d,]+\.?\d*)",
        ],
        "vendor_name": [
            r"(?:from|vendor|seller|billed?\s+from)[^\n:]*:\s*([A-Za-z][^\n]{2,50})",
        ],
    }

    field_patterns = patterns.get(field_name.lower(), [])
    if not field_patterns:
        return {"field": field_name, "found": False, "value": None,
                "note": "No regex pattern for this field; try re_extract_section."}

    for pattern in field_patterns:
        m = re.search(pattern, raw_text, re.IGNORECASE)
        if m:
            raw_val = m.group(1).strip()
            # Try to convert amounts to float
            if field_name in ("total_amount", "subtotal", "tax_amount"):
                try:
                    val = float(re.sub(r"[^\d.]", "", raw_val))
                    return {"field": field_name, "found": True, "value": val, "raw": raw_val}
                except ValueError:
                    pass
            return {"field": field_name, "found": True, "value": raw_val}

    return {"field": field_name, "found": False, "value": None,
            "context_hint": context_hint or "Pattern not found in document text."}


# ── Tool dispatch ──────────────────────────────────────────────────────────────

RESOLVER_TOOL_SPECS = [
    {
        "name": "re_extract_section",
        "description": (
            "Re-runs targeted extraction on specific fields that failed validation. "
            "Use this when values are missing or look wrong. The model will re-read "
            "the document focused only on those fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "focus_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of field names to re-extract (e.g. [\"total_amount\", \"subtotal\"]).",
                },
                "instruction": {
                    "type": "string",
                    "description": "Specific guidance for the extraction (e.g. 'The subtotal may include currency symbols like ₹ or $').",
                },
            },
            "required": ["focus_fields", "instruction"],
        },
    },
    {
        "name": "evaluate_math",
        "description": (
            "Safely evaluates an arithmetic expression and returns the result. "
            "Use to verify: does subtotal + tax == total? Or: do line items sum to subtotal?"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A simple math expression, e.g. '1234.56 + 98.44' or '10 * 25.99'.",
                },
            },
            "required": ["expression"],
        },
    },
    {
        "name": "lookup_field",
        "description": (
            "Searches the raw document text for a specific field using regex patterns. "
            "Faster than re_extract_section for simple fields. "
            "Supported fields: invoice_number, due_date, invoice_date, total_amount, subtotal, tax_amount, vendor_name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field_name": {
                    "type": "string",
                    "description": "Name of the field to look up.",
                },
                "context_hint": {
                    "type": "string",
                    "description": "Optional hint about where to find the value.",
                    "default": "",
                },
            },
            "required": ["field_name"],
        },
    },
]

OPENAI_RESOLVER_SPECS = [{"type": "function", "function": s} for s in RESOLVER_TOOL_SPECS]
GEMINI_RESOLVER_SPECS = {"function_declarations": RESOLVER_TOOL_SPECS}


def _build_system_prompt(
    document_type: str,
    issues: list[dict[str, Any]],
    extracted_data: dict[str, Any],
) -> str:
    issues_text = "\n".join(
        f"  - [{i.get('severity', 'warning').upper()}] {i.get('field', '?')}: {i.get('message', '')}"
        for i in issues
    )
    data_summary = json.dumps(
        {k: v for k, v in extracted_data.items() if v is not None},
        indent=2,
    )[:1000]

    return (
        f"You are an expert document resolver agent. A '{document_type}' document "
        f"has been extracted but validation found the following issues:\n\n"
        f"{issues_text}\n\n"
        f"Currently extracted data:\n{data_summary}\n\n"
        f"Your goal is to fix as many issues as possible using your tools:\n"
        f"  1. Use evaluate_math to verify arithmetic before making corrections.\n"
        f"  2. Use lookup_field or re_extract_section to find the correct values.\n"
        f"  3. Return your final answer as JSON (no markdown):\n"
        f"     {{\"corrections\": {{\"field_name\": corrected_value, ...}}, "
        f"     \"reasoning\": \"what you found and what you changed\", "
        f"     \"resolved\": true/false}}\n\n"
        f"Only include corrections where you found clear evidence. "
        f"Do not guess. If you cannot fix an issue, set resolved=false and explain why."
    )


async def _dispatch_resolver_tool(
    name: str,
    args: dict[str, Any],
    raw_text: str,
    document_type: str,
) -> Any:
    if name == "re_extract_section":
        return await _tool_re_extract_section(
            raw_text=raw_text,
            document_type=document_type,
            focus_fields=args.get("focus_fields", []),
            instruction=args.get("instruction", ""),
        )
    if name == "evaluate_math":
        return _tool_evaluate_math(args.get("expression", ""))
    if name == "lookup_field":
        return _tool_lookup_field(raw_text, args.get("field_name", ""), args.get("context_hint", ""))
    return {"error": f"Unknown tool: {name}"}


# ── OpenAI agent loop ──────────────────────────────────────────────────────────

async def _run_openai_resolver(
    raw_text: str,
    document_type: str,
    extracted_data: dict[str, Any],
    issues: list[dict[str, Any]],
) -> ResolverResult:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    system_prompt = _build_system_prompt(document_type, issues, extracted_data)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Please resolve the validation issues described above."},
    ]
    agent_steps: list[dict] = []

    for round_num in range(MAX_RESOLVER_ROUNDS):
        response = await client.chat.completions.create(
            model=settings.strong_model_name,
            messages=messages,
            tools=OPENAI_RESOLVER_SPECS,
            tool_choice="auto",
            temperature=0,
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = await _dispatch_resolver_tool(tc.function.name, args, raw_text, document_type)
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

        elif msg.content:
            try:
                raw = msg.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                answer = json.loads(raw.strip())

                corrections: dict[str, Any] = answer.get("corrections", {})
                resolved: bool = bool(answer.get("resolved", bool(corrections)))
                reasoning: str = answer.get("reasoning", "")

                # Apply corrections to the extracted data copy
                updated = dict(extracted_data)
                updated.update(corrections)

                return ResolverResult(
                    resolved=resolved,
                    extracted_data=updated,
                    reasoning=reasoning,
                    agent_steps=agent_steps,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                break

    return ResolverResult(
        resolved=False,
        extracted_data=extracted_data,
        reasoning=f"Resolver did not converge after {MAX_RESOLVER_ROUNDS} rounds.",
        agent_steps=agent_steps,
    )


# ── Gemini agent loop ──────────────────────────────────────────────────────────

async def _run_gemini_resolver(
    raw_text: str,
    document_type: str,
    extracted_data: dict[str, Any],
    issues: list[dict[str, Any]],
) -> ResolverResult:
    from app.utils.llm import get_llm
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

    system_prompt = _build_system_prompt(document_type, issues, extracted_data)
    
    llm = get_llm(model_type="strong", temperature=0.0)
    llm_with_tools = llm.bind_tools(RESOLVER_TOOL_SPECS)
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Please resolve the validation issues described above.")
    ]
    agent_steps: list[dict] = []

    for round_num in range(MAX_RESOLVER_ROUNDS):
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
                result = await _dispatch_resolver_tool(tool_call["name"], tool_call["args"], raw_text, document_type)
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
                raw_content = response.content
                if isinstance(raw_content, list):
                    raw_content = raw_content[0].get("text", "") if raw_content else ""
                raw = str(raw_content).strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                answer = json.loads(raw.strip())

                corrections: dict[str, Any] = answer.get("corrections", {})
                resolved: bool = bool(answer.get("resolved", bool(corrections)))
                reasoning: str = answer.get("reasoning", "")

                updated = dict(extracted_data)
                updated.update(corrections)

                return ResolverResult(
                    resolved=resolved,
                    extracted_data=updated,
                    reasoning=reasoning,
                    agent_steps=agent_steps,
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                break

    return ResolverResult(
        resolved=False,
        extracted_data=extracted_data,
        reasoning=f"Gemini resolver did not converge after {MAX_RESOLVER_ROUNDS} rounds.",
        agent_steps=agent_steps,
    )


# ── Public entry point ─────────────────────────────────────────────────────────

async def resolve_validation_issues(
    raw_text: str,
    document_type: str,
    extracted_data: dict[str, Any],
    issues: list[dict[str, Any]],
) -> ResolverResult:
    """
    Attempt to fix validation failures using an agent.

    Args:
        raw_text:       The raw parsed text of the document.
        document_type:  e.g. "invoice", "resume".
        extracted_data: The current extracted dict (may have errors).
        issues:         List of ValidationIssue dicts (field, severity, message).

    Returns:
        ResolverResult with corrected extracted_data, reasoning, and whether it resolved.
    """
    if not settings.llm_available:
        return ResolverResult(
            resolved=False,
            extracted_data=extracted_data,
            reasoning="No LLM available for resolution; escalating to human review.",
        )

    # Only trigger for error/warning severity, not info
    actionable = [i for i in issues if i.get("severity") in ("error", "warning")]
    if not actionable:
        return ResolverResult(
            resolved=True,
            extracted_data=extracted_data,
            reasoning="No actionable issues to resolve.",
        )

    try:
        provider = settings.llm_provider.lower()
        if provider == "openai":
            return await _run_openai_resolver(raw_text, document_type, extracted_data, actionable)
        elif provider == "gemini":
            return await _run_gemini_resolver(raw_text, document_type, extracted_data, actionable)
        else:
            return ResolverResult(
                resolved=False,
                extracted_data=extracted_data,
                reasoning=f"Unsupported LLM provider: {settings.llm_provider}",
            )
    except Exception as e:
        return ResolverResult(
            resolved=False,
            extracted_data=extracted_data,
            reasoning=f"Resolver agent failed: {str(e)[:200]}",
        )
