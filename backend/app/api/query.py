"""Query API — Natural language query interface for document intelligence."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    """Natural language query request."""
    question: str = Field(..., min_length=1, description="Natural language question")
    max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")


class QuerySource(BaseModel):
    """A source document referenced in the answer."""
    document_id: str | None = None
    filename: str | None = None
    relevance: float | None = None


class QueryStep(BaseModel):
    """A single step in the query agent's reasoning."""
    round: int
    tool: str
    input: dict
    output: dict


class QueryResponse(BaseModel):
    """Response from the query agent."""
    answer: str
    sources: list[QuerySource]
    agent_reasoning: str
    query_steps: list[QueryStep]


@router.post("", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Ask a natural language question about your documents.

    The query agent will use tools to search, filter, and aggregate
    data from your document collection, then synthesize a natural
    language answer with citations.

    Examples:
    - "What was Amazon's Q2 2026 revenue?"
    - "Show me all invoices over $10,000"
    - "How many documents have I uploaded?"
    - "What are the key findings in the financial report?"
    """
    from app.pipeline.query_agent import run_query_agent

    result = await run_query_agent(request.question)

    return QueryResponse(
        answer=result.answer,
        sources=[
            QuerySource(
                document_id=s.get("document_id"),
                filename=s.get("filename"),
                relevance=s.get("relevance"),
            )
            for s in result.sources
        ],
        agent_reasoning=result.agent_reasoning,
        query_steps=[
            QueryStep(
                round=step.get("round", 0),
                tool=step.get("tool", ""),
                input=step.get("input", {}),
                output=step.get("output", {}),
            )
            for step in result.query_steps
        ],
    )
