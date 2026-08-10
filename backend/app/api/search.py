"""Search API — hybrid search over extracted documents via Elasticsearch."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DocumentStatus
from app.schemas import SearchResponse, SearchResult
from app.elasticsearch import search_documents as es_search_documents
import uuid
import datetime

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search_documents(
    q: str = Query(..., min_length=1, description="Search query"),
    type_filter: str | None = Query(None, alias="type"),
    status_filter: DocumentStatus | None = Query(None, alias="status"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    min_confidence: float | None = Query(None, ge=0, le=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),  # Kept for dependency injection compatibility, though unused
):
    """
    Hybrid semantic + keyword search across all documents using Elasticsearch.

    Supports:
    - Natural language queries via embedding kNN search
    - Keyword matching
    - Faceted filtering by document type, status, date range, confidence
    - Relevance-ranked results with highlighted snippets
    """
    # Build Elasticsearch filter list
    filters = []
    
    if status_filter:
        filters.append({"term": {"status": status_filter.value if hasattr(status_filter, "value") else str(status_filter)}})
        
    if min_confidence is not None:
        filters.append({"range": {"confidence_score": {"gte": min_confidence}}})
        
    if date_from or date_to:
        date_range = {}
        if date_from:
            date_range["gte"] = date_from.isoformat()
        if date_to:
            date_range["lte"] = date_to.isoformat()
        filters.append({"range": {"created_at": date_range}})

    # Execute search against Elasticsearch
    es_result = await es_search_documents(
        query=q,
        document_type=type_filter,
        size=per_page,
        offset=(page - 1) * per_page,
        extra_filters=filters if filters else None,
    )

    total = es_result.get("total", 0)
    hits = es_result.get("hits", [])

    results = []
    for hit in hits:
        # Create headline from highlights
        highlights = hit.get("highlights", {})
        headline_parts = []
        for field, texts in highlights.items():
            headline_parts.extend(texts)
        headline = " ... ".join(headline_parts) if headline_parts else None

        results.append(
            SearchResult(
                id=uuid.UUID(hit.get("document_id")),
                original_filename=hit.get("original_filename", ""),
                document_type=hit.get("document_type"),
                status=DocumentStatus(hit.get("status", "pending")),
                confidence_score=hit.get("confidence_score"),
                relevance_score=round(hit.get("relevance_score", 0), 4),
                headline=headline,
                # Provide dummy or parsed date if actual date is needed; ES stores ISO format
                created_at=datetime.datetime.fromisoformat(hit.get("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat()).replace('Z', '+00:00')),
            )
        )

    return SearchResponse(
        results=results,
        total=total,
        query=q,
        page=page,
        per_page=per_page,
    )
