"""SQLAlchemy models for the Document Intelligence Platform."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Boolean,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from app.database import Base

from sqlalchemy.types import TypeDecorator, CHAR

# Cross-database compatible types (PostgreSQL in production, SQLite fallback for unit tests)
JSONType = JSONB().with_variant(JSON(), "sqlite")
TSVectorType = TSVECTOR().with_variant(Text(), "sqlite")


class UUIDType(TypeDecorator):
    """Platform-independent UUID type.
    Uses PostgreSQL's native UUID, or CHAR(36) for SQLite in tests.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            return uuid.UUID(value)
        return value



class DocumentStatus(str, enum.Enum):
    """Document processing status state machine."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class DocumentType(str, enum.Enum):
    """Supported document type classifications."""
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    RESUME = "resume"
    GENERIC = "generic"


class PipelineStage(str, enum.Enum):
    """Processing pipeline stages."""
    CLASSIFY = "classify"
    PARSE = "parse"
    EXTRACT = "extract"
    VALIDATE = "validate"


class StageStatus(str, enum.Enum):
    """Status of an individual pipeline stage."""
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Document(Base):
    """
    Core document record. Stores metadata, processing state,
    extracted data (JSONB), and the full-text search vector.
    """
    __tablename__ = "documents"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    original_filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)

    # Processing state
    status = Column(
        Enum(DocumentStatus),
        nullable=False,
        default=DocumentStatus.PENDING,
        index=True,
    )
    document_type = Column(
        Enum(DocumentType),
        nullable=True,
        index=True,
    )
    confidence_score = Column(Float, nullable=True)

    # Content
    raw_text = Column(Text, nullable=True)
    extracted_data = Column(JSONType, nullable=True, default=dict)

    # Full-text search vector (populated by trigger)
    search_vector = Column(TSVectorType, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    extracted_fields = relationship(
        "ExtractedField",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    processing_logs = relationship(
        "ProcessingLog",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ProcessingLog.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_documents_search_vector", "search_vector", postgresql_using="gin"),
        Index("idx_documents_extracted_data", "extracted_data", postgresql_using="gin"),
        Index("idx_documents_created_at", "created_at"),
    )


class ExtractedField(Base):
    """
    Normalized extracted field for cross-document queries and
    per-field confidence tracking / human corrections.
    """
    __tablename__ = "extracted_fields"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUIDType,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    field_name = Column(String(200), nullable=False)
    field_value = Column(Text, nullable=True)
    field_type = Column(String(50), nullable=True)  # string, number, date, currency, etc.
    confidence = Column(Float, nullable=True)
    human_verified = Column(Boolean, default=False, nullable=False)
    source_location = Column(String(200), nullable=True)  # e.g., "page 1, block 3"

    # Relationships
    document = relationship("Document", back_populates="extracted_fields")

    __table_args__ = (
        Index("idx_extracted_fields_field_name", "field_name"),
        Index("idx_extracted_fields_field_name_value", "field_name", "field_value"),
    )


class ProcessingLog(Base):
    """
    Audit trail for each pipeline stage. Provides full observability
    into why a document succeeded, failed, or needs review.
    """
    __tablename__ = "processing_logs"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUIDType,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    stage = Column(Enum(PipelineStage), nullable=False)
    status = Column(Enum(StageStatus), nullable=False)
    duration_ms = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSONType, nullable=True, default=dict)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    document = relationship("Document", back_populates="processing_logs")
