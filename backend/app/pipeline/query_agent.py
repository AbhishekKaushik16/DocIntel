"""
Query Agent — Natural Language Interface for Document Intelligence

An agentic query engine that translates natural language questions into
optimized database/search queries and synthesizes answers.

Tools available to the agent:
  • search_documents    — Full-text + structured search via Elasticsearch
  • query_jsonb         — PostgreSQL JSONB path queries over extracted_data
  • aggregate_stats     — Aggregation queries (count, sum, avg) across documents
  • get_document_detail — Retrieve full extracted data for a specific document
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.utils.rate_limit import throttle_gemini_request

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Result of a query agent invocation."""
    answer: str
    sources: list[dict[str, Any]]
    agent_reasoning: str
    query_steps: list[dict[str, Any]] = field(default_factory=list)


# ── Tool implementations ───────────────────────────────────────────────────────

async def _tool_search_documents(query: str, document_type: str | None = None, max_results: int = 5) -> dict:
    """Search across all documents using Elasticsearch."""
    try:
        from app.elasticsearch import search_documents
        result = await search_documents(
            query=query,
            document_type=document_type,
            size=max_results,
        )
        return {
            "total_matches": result.get("total", 0),
            "results": [
                {
                    "document_id": h.get("document_id"),
                    "filename": h.get("original_filename"),
                    "type": h.get("document_type"),
                    "confidence": h.get("confidence_score"),
                    "highlights": h.get("highlights", {}),
                }
                for h in result.get("hits", [])[:max_results]
            ],
        }
    except Exception as e:
        # Fallback to PostgreSQL full-text search
        return await _tool_search_postgres_fallback(query, document_type, max_results)


async def _tool_search_postgres_fallback(query: str, document_type: str | None, max_results: int) -> dict:
    """Fallback search using PostgreSQL tsvector when ES is unavailable."""
    from sqlalchemy import func, select, text
    from app.database import async_session_factory
    from app.models import Document

    async with async_session_factory() as db:
        ts_query = func.plainto_tsquery("english", query)
        stmt = (
            select(
                Document.id,
                Document.original_filename,
                Document.document_type,
                Document.confidence_score,
                func.ts_rank_cd(Document.search_vector, ts_query).label("rank"),
            )
            .where(Document.search_vector.op("@@")(ts_query))
        )
        if document_type:
            stmt = stmt.where(Document.document_type == document_type)
        stmt = stmt.order_by(text("rank DESC")).limit(max_results)

        result = await db.execute(stmt)
        rows = result.all()
        return {
            "total_matches": len(rows),
            "results": [
                {
                    "document_id": str(r.id),
                    "filename": r.original_filename,
                    "type": r.document_type,
                    "confidence": r.confidence_score,
                }
                for r in rows
            ],
        }


async def _tool_query_jsonb(json_path: str, value: str | None = None, operator: str = "contains") -> dict:
    """
    Query extracted_data JSONB field in PostgreSQL.

    Examples:
      json_path="contacts.investor_relations.email" → finds documents with that path
      json_path="total_amount", value="1000", operator="gte" → numeric comparison
    """
    from sqlalchemy import select, text, cast, String
    from app.database import async_session_factory
    from app.models import Document

    async with async_session_factory() as db:
        try:
            # Build the JSONB query based on operator
            path_parts = json_path.split(".")
            jsonb_path = "extracted_data"
            for part in path_parts:
                jsonb_path += f"->'{part}'"

            # Replace last -> with ->> for text comparison
            last_arrow = jsonb_path.rfind("->")
            jsonb_path_text = jsonb_path[:last_arrow] + "->>" + jsonb_path[last_arrow + 2:]

            if value and operator == "gte":
                where_clause = text(f"({jsonb_path_text})::numeric >= :val")
                params = {"val": float(value)}
            elif value and operator == "lte":
                where_clause = text(f"({jsonb_path_text})::numeric <= :val")
                params = {"val": float(value)}
            elif value and operator == "equals":
                where_clause = text(f"{jsonb_path_text} = :val")
                params = {"val": value}
            elif value and operator == "contains":
                where_clause = text(f"{jsonb_path_text} ILIKE :val")
                params = {"val": f"%{value}%"}
            else:
                # Just check if the path exists and is not null
                where_clause = text(f"{jsonb_path_text} IS NOT NULL")
                params = {}

            stmt = (
                select(
                    Document.id,
                    Document.original_filename,
                    Document.document_type,
                    text(f"{jsonb_path_text} as field_value"),
                )
                .where(where_clause)
                .limit(10)
            )

            result = await db.execute(stmt, params)
            rows = result.all()

            return {
                "query": f"{json_path} {operator} {value or 'exists'}",
                "total_matches": len(rows),
                "results": [
                    {
                        "document_id": str(r.id),
                        "filename": r.original_filename,
                        "type": r.document_type,
                        "value": r.field_value,
                    }
                    for r in rows
                ],
            }
        except Exception as e:
            return {"query": json_path, "error": str(e), "total_matches": 0, "results": []}


async def _tool_aggregate_stats(
    group_by: str | None = None,
    metric: str = "count",
    json_field: str | None = None,
) -> dict:
    """
    Run aggregation queries across documents.

    Examples:
      group_by="document_type", metric="count" → count docs per type
      metric="avg", json_field="confidence_score" → average confidence
    """
    from sqlalchemy import func, select, text
    from app.database import async_session_factory
    from app.models import Document, DocumentStatus

    async with async_session_factory() as db:
        try:
            if group_by == "document_type":
                stmt = (
                    select(Document.document_type, func.count(Document.id))
                    .group_by(Document.document_type)
                )
                result = await db.execute(stmt)
                rows = result.all()
                return {
                    "aggregation": "count by document_type",
                    "results": {r[0] or "unknown": r[1] for r in rows},
                }

            elif group_by == "status":
                stmt = (
                    select(Document.status, func.count(Document.id))
                    .group_by(Document.status)
                )
                result = await db.execute(stmt)
                rows = result.all()
                return {
                    "aggregation": "count by status",
                    "results": {r[0].value if hasattr(r[0], "value") else str(r[0]): r[1] for r in rows},
                }

            elif metric == "avg" and json_field == "confidence_score":
                stmt = select(func.avg(Document.confidence_score))
                result = await db.execute(stmt)
                avg = result.scalar()
                return {
                    "aggregation": "average confidence_score",
                    "result": round(float(avg), 4) if avg else None,
                }

            elif metric == "count":
                stmt = select(func.count(Document.id))
                result = await db.execute(stmt)
                count = result.scalar()
                return {"aggregation": "total document count", "result": count}

            return {"error": f"Unsupported aggregation: {metric} / {group_by}"}

        except Exception as e:
            return {"error": str(e)}


async def _tool_get_document_detail(document_id: str) -> dict:
    """Retrieve full extracted data for a specific document."""
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.models import Document

    async with async_session_factory() as db:
        try:
            import uuid
            doc_uuid = uuid.UUID(document_id)
            result = await db.execute(select(Document).where(Document.id == doc_uuid))
            doc = result.scalar_one_or_none()
            if not doc:
                return {"error": f"Document {document_id} not found"}

            return {
                "document_id": str(doc.id),
                "filename": doc.original_filename,
                "type": doc.document_type,
                "status": doc.status.value if hasattr(doc.status, "value") else str(doc.status),
                "confidence": doc.confidence_score,
                "extracted_data": doc.extracted_data or {},
            }
        except Exception as e:
            return {"error": str(e)}


# ── Tool dispatch ──────────────────────────────────────────────────────────────

QUERY_TOOL_SPECS = [
    {
        "name": "search_documents",
        "description": (
            "Search across all documents using full-text search. "
            "Use this to find documents matching a topic, keyword, or entity. "
            "Returns matching documents with relevance scores and highlighted snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query text."},
                "document_type": {"type": "string", "description": "Optional filter by document type (e.g. 'financial_report', 'invoice')."},
                "max_results": {"type": "integer", "description": "Max results to return (default 5).", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_jsonb",
        "description": (
            "Query specific fields in extracted document data using dot-notation paths. "
            "Use this to find documents where a specific extracted field has a certain value. "
            "Examples: json_path='total_amount' value='1000' operator='gte' finds invoices over $1000. "
            "json_path='vendor_name' value='Amazon' operator='contains' finds Amazon documents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "json_path": {"type": "string", "description": "Dot-notation path into extracted_data (e.g. 'total_amount', 'contacts.email')."},
                "value": {"type": "string", "description": "Value to match against (optional if just checking existence)."},
                "operator": {
                    "type": "string",
                    "description": "Comparison operator: 'contains', 'equals', 'gte', 'lte'.",
                    "default": "contains",
                },
            },
            "required": ["json_path"],
        },
    },
    {
        "name": "aggregate_stats",
        "description": (
            "Run aggregation queries across all documents. "
            "Use this for questions like 'how many documents are there', "
            "'what's the average confidence', or 'count by document type'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "description": "Group by field: 'document_type' or 'status'."},
                "metric": {"type": "string", "description": "Aggregation metric: 'count' or 'avg'.", "default": "count"},
                "json_field": {"type": "string", "description": "Field for avg/sum metric (e.g. 'confidence_score')."},
            },
        },
    },
    {
        "name": "get_document_detail",
        "description": (
            "Retrieve the full extracted data for a specific document by its ID. "
            "Use this after finding a document via search to get its complete extracted data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "UUID of the document."},
            },
            "required": ["document_id"],
        },
    },
]


GEMINI_QUERY_TOOLS = {"function_declarations": QUERY_TOOL_SPECS}


async def _dispatch_query_tool(name: str, args: dict) -> Any:
    """Execute a query tool by name."""
    if name == "search_documents":
        return await _tool_search_documents(
            query=args.get("query", ""),
            document_type=args.get("document_type"),
            max_results=args.get("max_results", 5),
        )
    if name == "query_jsonb":
        return await _tool_query_jsonb(
            json_path=args.get("json_path", ""),
            value=args.get("value"),
            operator=args.get("operator", "contains"),
        )
    if name == "aggregate_stats":
        return await _tool_aggregate_stats(
            group_by=args.get("group_by"),
            metric=args.get("metric", "count"),
            json_field=args.get("json_field"),
        )
    if name == "get_document_detail":
        return await _tool_get_document_detail(args.get("document_id", ""))
    return {"error": f"Unknown tool: {name}"}


# ── Agent loop ─────────────────────────────────────────────────────────────────

QUERY_SYSTEM_PROMPT = (
    "You are an intelligent document query agent for a Document Intelligence Platform. "
    "Users ask natural language questions about their uploaded documents. "
    "You have tools to search documents, query specific fields, and run aggregations.\n\n"
    "Strategy:\n"
    "1. Understand what the user is asking for.\n"
    "2. Use search_documents for general keyword/topic queries.\n"
    "3. Use query_jsonb for specific field value lookups.\n"
    "4. Use aggregate_stats for counting/averaging across documents.\n"
    "5. Use get_document_detail to dive deep into a specific document.\n"
    "6. You may chain multiple tools to answer complex questions.\n\n"
    "When you have enough information, return a clear, concise answer. "
    "Always cite which document(s) your answer came from. "
    "Return your final answer as JSON:\n"
    '{"answer": "Your natural language answer", '
    '"sources": [{"document_id": "...", "filename": "...", "relevance": 0.95}], '
    '"reasoning": "Brief explanation of how you found the answer"}'
)

MAX_QUERY_ROUNDS = 6


async def run_query_agent(question: str) -> QueryResult:
    """
    Run the query agent to answer a natural language question.

    Uses Gemini with function calling to iteratively query the database
    and synthesize an answer.
    """
    if not settings.llm_available:
        return QueryResult(
            answer="LLM is not available. Please configure an API key.",
            sources=[],
            agent_reasoning="No LLM configured.",
        )

    model = settings.fast_model_name
    if not model.startswith("models/"):
        model = f"models/{model}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key}

    contents = [
        {
            "role": "user",
            "parts": [{"text": f"{QUERY_SYSTEM_PROMPT}\n\nUser question: {question}"}],
        }
    ]
    agent_steps: list[dict] = []

    async with httpx.AsyncClient(timeout=60) as client:
        for round_num in range(MAX_QUERY_ROUNDS):
            payload = {
                "contents": contents,
                "tools": [GEMINI_QUERY_TOOLS],
                "generationConfig": {"temperature": 0},
            }

            await throttle_gemini_request()
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code in (429, 503):
                # Rate limited — return partial answer
                return QueryResult(
                    answer="I'm currently rate-limited. Please try again in a moment.",
                    sources=[],
                    agent_reasoning=f"Rate limited after {round_num} rounds.",
                    query_steps=agent_steps,
                )

            resp.raise_for_status()
            body = resp.json()

            candidate = body["candidates"][0]
            parts = candidate["content"]["parts"]

            # Collect tool calls and text
            tool_calls = []
            text_part = None
            for part in parts:
                if "functionCall" in part:
                    tool_calls.append(part["functionCall"])
                elif "text" in part:
                    text_part = part["text"]

            if tool_calls:
                # Execute tools
                contents.append({"role": "model", "parts": parts})
                tool_response_parts = []

                for tc in tool_calls:
                    result = await _dispatch_query_tool(tc["name"], tc.get("args", {}))
                    agent_steps.append({
                        "round": round_num + 1,
                        "tool": tc["name"],
                        "input": tc.get("args", {}),
                        "output": result,
                    })
                    tool_response_parts.append({
                        "functionResponse": {
                            "name": tc["name"],
                            "response": {"result": result},
                        }
                    })
                contents.append({"role": "user", "parts": tool_response_parts})

            elif text_part:
                # Agent is done — parse the answer
                try:
                    raw = text_part.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    data = json.loads(raw.strip())
                    return QueryResult(
                        answer=data.get("answer", text_part),
                        sources=data.get("sources", []),
                        agent_reasoning=data.get("reasoning", ""),
                        query_steps=agent_steps,
                    )
                except (json.JSONDecodeError, KeyError):
                    # Model returned free-text — use it directly
                    return QueryResult(
                        answer=text_part,
                        sources=[],
                        agent_reasoning="Agent returned free-text answer.",
                        query_steps=agent_steps,
                    )
            else:
                break

    return QueryResult(
        answer="I couldn't find a definitive answer. Please try rephrasing your question.",
        sources=[],
        agent_reasoning=f"Agent did not converge after {MAX_QUERY_ROUNDS} rounds.",
        query_steps=agent_steps,
    )
