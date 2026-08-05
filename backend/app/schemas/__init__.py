"""Pydantic schemas for API request/response serialization."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, PipelineStage, StageStatus


# ──────────────────────────────────────────────────────────────
# Response schemas
# ──────────────────────────────────────────────────────────────


class ExtractedFieldResponse(BaseModel):
    """Single extracted field with confidence metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_name: str
    field_value: str | None
    field_type: str | None
    confidence: float | None
    human_verified: bool
    source_location: str | None


class ProcessingLogResponse(BaseModel):
    """A single pipeline stage log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stage: PipelineStage
    status: StageStatus
    duration_ms: int | None
    metadata_: dict[str, Any] | None = Field(alias="metadata_")
    error_message: str | None
    created_at: datetime


class DocumentResponse(BaseModel):
    """Full document response with extracted data and processing logs."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    mime_type: str | None
    file_size_bytes: int | None
    status: DocumentStatus
    document_type: str | None
    confidence_score: float | None
    extracted_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    extracted_fields: list[ExtractedFieldResponse] = []
    processing_logs: list[ProcessingLogResponse] = []


class DocumentListItem(BaseModel):
    """Lightweight document representation for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    mime_type: str | None
    file_size_bytes: int | None
    status: DocumentStatus
    document_type: str | None
    confidence_score: float | None
    created_at: datetime
    processed_at: datetime | None


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    documents: list[DocumentListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


# ──────────────────────────────────────────────────────────────
# Request schemas
# ──────────────────────────────────────────────────────────────


class FieldCorrectionRequest(BaseModel):
    """Human correction for a specific extracted field."""

    field_name: str
    field_value: str


class FieldCorrectionsRequest(BaseModel):
    """Batch of field corrections from the human review interface."""

    corrections: list[FieldCorrectionRequest]


# ──────────────────────────────────────────────────────────────
# Search schemas
# ──────────────────────────────────────────────────────────────


class SearchResult(BaseModel):
    """A single search result with relevance score and highlighted snippet."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    document_type: str | None
    status: DocumentStatus
    confidence_score: float | None
    relevance_score: float | None = None
    headline: str | None = None  # highlighted matching text
    created_at: datetime


class SearchResponse(BaseModel):
    """Paginated search results."""

    results: list[SearchResult]
    total: int
    query: str
    page: int
    per_page: int


# ──────────────────────────────────────────────────────────────
# Stats schemas
# ──────────────────────────────────────────────────────────────


class DashboardStats(BaseModel):
    """Aggregate statistics for the dashboard."""

    total_documents: int
    completed: int
    processing: int
    needs_review: int
    failed: int
    by_type: dict[str, int]
    avg_confidence: float | None


class UploadResponse(BaseModel):
    """Response after a file upload."""

    id: uuid.UUID
    original_filename: str
    status: DocumentStatus
    message: str
