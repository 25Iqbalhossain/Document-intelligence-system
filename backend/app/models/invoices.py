"""Invoices, line items, validation results, relationships, embeddings, corrections."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    MONEY_PRECISION,
    MONEY_SCALE,
    Base,
    MatchStatus,
    RelationType,
    ResolutionStatus,
    ReviewStatus,
    Severity,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.documents import _enum

EMBEDDING_DIM = 384  # fixed at migration time; changing it requires a migration and re-embedding


def _money(nullable: bool = True) -> Mapped[Decimal | None]:
    return mapped_column(Numeric(MONEY_PRECISION, MONEY_SCALE), nullable=nullable)


def _json_default() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "invoices"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Spreadsheet/CSV rows map to invoices too; row_ref identifies sheet+row for traceability.
    source_kind: Mapped[str] = mapped_column(String(16), default="document", nullable=False)
    row_ref: Mapped[str | None] = mapped_column(String(128))

    # NULL = not found in the source. Never defaulted to 0 or "".
    invoice_number: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    supplier_name: Mapped[str | None] = mapped_column(String(512))
    customer_name: Mapped[str | None] = mapped_column(String(512))
    subtotal: Mapped[Decimal | None] = _money()
    tax: Mapped[Decimal | None] = _money()
    total: Mapped[Decimal | None] = _money()
    currency: Mapped[str | None] = mapped_column(String(8))

    confidence: Mapped[dict[str, Any]] = _json_default()  # per-field extraction confidence
    source_references: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    raw_extraction: Mapped[dict[str, Any]] = _json_default()  # original model output, untouched
    original_text: Mapped[dict[str, Any]] = _json_default()  # original extracted strings per field

    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    requires_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_status: Mapped[ReviewStatus] = mapped_column(
        _enum(ReviewStatus), default=ReviewStatus.not_required, nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # optimistic locking

    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLineItem.line_no"
    )
    validation_results: Mapped[list["ValidationResult"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}

    __table_args__ = (
        Index("ix_invoices_dup_lookup", "invoice_number", "supplier_name", "invoice_date"),
        Index("ix_invoices_supplier", "supplier_name"),
        Index("ix_invoices_date", "invoice_date"),
        Index("ix_invoices_total", "total"),
        Index("ix_invoices_review_status", "review_status"),
        # Duplicate lookups compare canonical keys ('Inv-001' == 'INV 001'); index the same expression.
        Index("ix_invoices_number_key", text("regexp_replace(upper(invoice_number), '[^A-Z0-9]', '', 'g')")),
    )


class InvoiceLineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal | None] = _money()
    tax: Mapped[Decimal | None] = _money()
    total: Mapped[Decimal | None] = _money()
    confidence: Mapped[dict[str, Any]] = _json_default()
    source_references: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    original_text: Mapped[dict[str, Any]] = _json_default()

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")

    __table_args__ = (UniqueConstraint("invoice_id", "line_no", name="uq_invoice_line_items_invoice_line"),)


class ValidationResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "validation_results"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(128))  # e.g. 'total' or 'line_items[2].total'
    severity: Mapped[Severity] = mapped_column(_enum(Severity), nullable=False)
    # Stored as text so any field type (money, date, string) round-trips exactly.
    expected_value: Mapped[str | None] = mapped_column(Text)
    actual_value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_references: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    resolution_status: Mapped[ResolutionStatus] = mapped_column(
        _enum(ResolutionStatus), default=ResolutionStatus.open, nullable=False
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    invoice: Mapped[Invoice] = relationship(back_populates="validation_results")

    __table_args__ = (
        Index("ix_validation_results_rule", "rule_id"),
        Index("ix_validation_results_open", "resolution_status", "severity"),
    )


class DocumentRelationship(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reconciliation and duplicate links. Fuzzy matches stay 'proposed' until a human confirms."""

    __tablename__ = "document_relationships"

    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    target_invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    relation_type: Mapped[RelationType] = mapped_column(_enum(RelationType), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    # match_method: exact_supplier | exact_number | fuzzy | attributes | duplicate
    match_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[MatchStatus] = mapped_column(
        _enum(MatchStatus), default=MatchStatus.proposed, nullable=False
    )
    discrepancies: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True
    )  # groups results of one reconciliation run
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (Index("ix_document_relationships_status", "status"),)


class ReconciliationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One reconciliation execution: which documents were compared, the config used and the outcome summary."""

    __tablename__ = "reconciliation_runs"

    left_document_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    right_document_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    # counts plus unmatched / duplicate / ambiguous records (ids and labels), so the report needs no recomputation
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DocumentEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_embeddings"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", "chunk_index", "model_name", name="uq_document_embeddings_chunk"
        ),
        Index(
            "ix_document_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ManualCorrection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only audit trail of human edits."""

    __tablename__ = "manual_corrections"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_line_items.id", ondelete="SET NULL"))
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
