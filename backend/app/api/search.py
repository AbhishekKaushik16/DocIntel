"""Search API — full-text search with faceted filtering over extracted documents."""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Document, DocumentStatus, DocumentType
from app.schemas import SearchResponse, SearchResult

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search_documents(
    q: str = Query(..., min_length=1, description="Search query"),
    type_filter: DocumentType | None = Query(None, alias="type"),
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    min_confidence: float | None = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Full-text search across all documents using PostgreSQL tsvector.

    Supports:
    - Natural language queries with stemming and ranking
    - Faceted filtering by document type, status, date range, confidence
    - Relevance-ranked results with highlighted snippets
    """
    # Build the tsquery from the user's input
    # plainto_tsquery handles natural language input gracefully
    ts_query = func.plainto_tsquery("english", q)

    # Base query: match against search_vector
    query = select(
        Document,
        func.ts_rank_cd(Document.search_vector, ts_query).label("relevance_score"),
        func.ts_headline(
            "english",
            func.coalesce(Document.raw_text, cast(Document.original_filename, String)),
            ts_query,
            text("'StartSel=<mark>, StopSel=</mark>, MaxWords=50, MinWords=20'"),
        ).label("headline"),
    ).where(Document.search_vector.op("@@")(ts_query))

    # Apply filters
    if type_filter:
        query = query.where(Document.document_type == type_filter)
    if status_filter:
        query = query.where(Document.status == status_filter)
    if date_from:
        query = query.where(func.date(Document.created_at) >= date_from)
    if date_to:
        query = query.where(func.date(Document.created_at) <= date_to)
    if min_confidence is not None:
        query = query.where(Document.confidence_score >= min_confidence)

    # Count total results
    count_subq = query.subquery()
    count_result = await db.execute(select(func.count()).select_from(count_subq))
    total = count_result.scalar() or 0

    # Order by relevance, paginate
    query = query.order_by(text("relevance_score DESC"))
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    rows = result.all()

    results = [
        SearchResult(
            id=row.Document.id,
            original_filename=row.Document.original_filename,
            document_type=row.Document.document_type,
            status=row.Document.status,
            confidence_score=row.Document.confidence_score,
            relevance_score=round(row.relevance_score, 4) if row.relevance_score else None,
            headline=row.headline,
            created_at=row.Document.created_at,
        )
        for row in rows
    ]

    return SearchResponse(
        results=results,
        total=total,
        query=q,
        page=page,
        per_page=per_page,
    )
