"""Query API — Natural language query interface for document intelligence."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    """Natural language query request."""
    question: str = Field(..., min_length=1, description="Natural language question")
    max_results: int = Field(10, ge=1, le=50, description="Maximum results to return")
    chat_history: list[dict[str, str]] = Field(default_factory=list, description="Previous conversation history")


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


@router.post("")
async def query_documents(request: QueryRequest):
    """
    Ask a natural language question about your documents.
    """
    from app.pipeline.query_agent import stream_query_agent

    return StreamingResponse(
        stream_query_agent(request.question, request.chat_history),
        media_type="text/event-stream",
    )
