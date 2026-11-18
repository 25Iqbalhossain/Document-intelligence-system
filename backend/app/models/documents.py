"""Users, documents, pages, jobs, outbox, extracted text, history."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    DocumentStatus,
    DocumentType,
    JobStage,
    JobStatus,
    PageKind,
    SoftDeleteMixin,
    TimestampMixin,
    UserRole,
    UUIDPrimaryKeyMixin,
)


def _enum(e: type) -> Enum:
    # native_enum=False keeps migrations simple (VARCHAR + CHECK) and additive.
    return Enum(e, native_enum=False, length=32, create_constraint=True, validate_strings=True)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), default=UserRole.viewer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "documents"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    doc_type: Mapped[DocumentType] = mapped_column(
        _enum(DocumentType), default=DocumentType.unknown, nullable=False
    )
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), default=DocumentStatus.uploaded, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        # Same content may be re-uploaded after soft delete; duplicates among live docs are
        # surfaced to the user, not blocked, so this index is non-unique.
        Index("ix_documents_sha256_live", "sha256", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_documents_status", "status"),
        Index("ix_documents_doc_type", "doc_type"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_metadata_gin", "metadata", postgresql_using="gin"),
    )


class DocumentPage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per PDF page, image, sheet or CSV. Nothing is skipped silently: failures are recorded."""

    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based; sheet index for Excel
    label: Mapped[str | None] = mapped_column(String(255))  # sheet name for spreadsheets
    kind: Mapped[PageKind] = mapped_column(_enum(PageKind), nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)  # 0-100; distinct from extraction confidence
    text_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="pages")

    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_document_pages_doc_page"),)


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[JobStage] = mapped_column(_enum(JobStage), nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.pending, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="jobs")

    __table_args__ = (Index("ix_processing_jobs_status_stage", "status", "stage"),)


class Outbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Transactional outbox: inserted in the same transaction as the job, relayed to the broker."""

    __tablename__ = "outbox"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_outbox_unpublished", "created_at", postgresql_where=text("published_at IS NULL")),
    )


class ExtractedText(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "extracted_text"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # native | ocr | table
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True)
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", "source", name="uq_extracted_text_doc_page_source"),
        Index("ix_extracted_text_tsv", "tsv", postgresql_using="gin"),
    )


class ProcessingHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only event log."""

    __tablename__ = "processing_history"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("processing_jobs.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
