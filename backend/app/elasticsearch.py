"""
Elasticsearch integration for DocIntel.

Provides:
  • Client setup with lazy connection
  • Index creation with dynamic mapping
  • Document indexing (called from orchestrator after processing)
  • Search helpers (used by the query agent and search API)
"""

import logging
from datetime import datetime
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Index name
DOCUMENT_INDEX = "docintel_documents"

# Index settings with dynamic mapping for flexible schemas
INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "document_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop", "snowball"],
                }
            }
        },
    },
    "mappings": {
        "dynamic": True,
        "properties": {
            "document_id": {"type": "keyword"},
            "original_filename": {
                "type": "text",
                "analyzer": "document_analyzer",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "document_type": {"type": "keyword"},
            "status": {"type": "keyword"},
            "confidence_score": {"type": "float"},
            "raw_text": {"type": "text", "analyzer": "document_analyzer"},
            "extracted_data": {"type": "object", "dynamic": True},
            "created_at": {"type": "date"},
            "indexed_at": {"type": "date"},
        },
    },
}


# ── Lazy client ────────────────────────────────────────────────────────────────

_es_client = None


async def get_es_client():
    """Get or create the Elasticsearch async client."""
    global _es_client
    if _es_client is None:
        from elasticsearch import AsyncElasticsearch
        _es_client = AsyncElasticsearch(
            settings.elasticsearch_url,
            request_timeout=30,
            max_retries=2,
            retry_on_timeout=True,
        )
    return _es_client


async def ensure_index():
    """Create the index if it doesn't exist."""
    try:
        es = await get_es_client()
        exists = await es.indices.exists(index=DOCUMENT_INDEX)
        if not exists:
            await es.indices.create(index=DOCUMENT_INDEX, body=INDEX_SETTINGS)
            logger.info(f"Created Elasticsearch index: {DOCUMENT_INDEX}")
    except Exception as e:
        logger.warning(f"Could not create ES index: {e}")


# ── Indexing ───────────────────────────────────────────────────────────────────

async def index_document(
    document_id: str,
    original_filename: str,
    document_type: str | None,
    status: str,
    confidence_score: float | None,
    raw_text: str | None,
    extracted_data: dict[str, Any] | None,
    created_at: datetime | None,
) -> None:
    """Index a processed document into Elasticsearch."""
    try:
        es = await get_es_client()
        await ensure_index()

        doc_body = {
            "document_id": document_id,
            "original_filename": original_filename,
            "document_type": document_type,
            "status": status,
            "confidence_score": confidence_score,
            "raw_text": (raw_text or "")[:100_000],  # Limit raw text size
            "extracted_data": extracted_data or {},
            "created_at": created_at.isoformat() if created_at else None,
            "indexed_at": datetime.utcnow().isoformat(),
        }

        await es.index(
            index=DOCUMENT_INDEX,
            id=document_id,
            document=doc_body,
        )
        logger.info(f"Indexed document {document_id} to Elasticsearch")
    except Exception as e:
        logger.warning(f"Failed to index document {document_id} to ES: {e}")


# ── Search ─────────────────────────────────────────────────────────────────────

async def search_documents(
    query: str,
    document_type: str | None = None,
    status: str | None = None,
    min_confidence: float | None = None,
    size: int = 20,
    from_: int = 0,
) -> dict[str, Any]:
    """
    Full-text + structured search over documents.

    Searches across raw_text, original_filename, and extracted_data fields
    with fuzzy matching and relevance ranking.
    """
    try:
        es = await get_es_client()

        # Build the query
        must_clauses = []
        filter_clauses = []

        # Full-text query across multiple fields
        if query:
            must_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": [
                        "original_filename^3",
                        "raw_text",
                        "extracted_data.*^2",
                    ],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            })

        # Filters
        if document_type:
            filter_clauses.append({"term": {"document_type": document_type}})
        if status:
            filter_clauses.append({"term": {"status": status}})
        if min_confidence is not None:
            filter_clauses.append({"range": {"confidence_score": {"gte": min_confidence}}})

        body = {
            "query": {
                "bool": {
                    "must": must_clauses or [{"match_all": {}}],
                    "filter": filter_clauses,
                }
            },
            "highlight": {
                "fields": {
                    "raw_text": {"fragment_size": 150, "number_of_fragments": 3},
                    "extracted_data.*": {"fragment_size": 150, "number_of_fragments": 2},
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
            "size": size,
            "from": from_,
            "_source": {
                "excludes": ["raw_text"],  # Don't return full text in search results
            },
        }

        result = await es.search(index=DOCUMENT_INDEX, body=body)

        hits = []
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            highlights = hit.get("highlight", {})
            hits.append({
                "document_id": source.get("document_id"),
                "original_filename": source.get("original_filename"),
                "document_type": source.get("document_type"),
                "status": source.get("status"),
                "confidence_score": source.get("confidence_score"),
                "relevance_score": hit["_score"],
                "highlights": highlights,
                "created_at": source.get("created_at"),
            })

        return {
            "hits": hits,
            "total": result["hits"]["total"]["value"],
        }

    except Exception as e:
        logger.error(f"Elasticsearch search failed: {e}")
        return {"hits": [], "total": 0, "error": str(e)}


async def search_extracted_data(
    query: str,
    field_path: str | None = None,
    size: int = 20,
) -> dict[str, Any]:
    """
    Search specifically within extracted_data fields.
    Useful for the query agent to find specific field values.
    """
    try:
        es = await get_es_client()

        if field_path:
            search_field = f"extracted_data.{field_path}"
        else:
            search_field = "extracted_data.*"

        body = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": [search_field],
                    "fuzziness": "AUTO",
                }
            },
            "size": size,
            "_source": ["document_id", "original_filename", "document_type", "extracted_data"],
        }

        result = await es.search(index=DOCUMENT_INDEX, body=body)

        return {
            "hits": [
                {
                    "document_id": hit["_source"].get("document_id"),
                    "original_filename": hit["_source"].get("original_filename"),
                    "extracted_data": hit["_source"].get("extracted_data", {}),
                    "score": hit["_score"],
                }
                for hit in result["hits"]["hits"]
            ],
            "total": result["hits"]["total"]["value"],
        }

    except Exception as e:
        logger.error(f"Elasticsearch extracted data search failed: {e}")
        return {"hits": [], "total": 0, "error": str(e)}
