"""Declarative base, shared column mixins and enums."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Money: NUMERIC(18,4). NULL means "unknown", never zero.
MONEY_PRECISION = 18
MONEY_SCALE = 4


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRole(str, enum.Enum):
    admin = "admin"
    reviewer = "reviewer"
    uploader = "uploader"
    viewer = "viewer"


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    validating = "validating"
    classified = "classified"
    extracting_text = "extracting_text"
    ai_extracting = "ai_extracting"
    validating_financials = "validating_financials"
    embedding = "embedding"
    needs_review = "needs_review"
    processed = "processed"
    approved = "approved"
    failed = "failed"
    cancelled = "cancelled"


class DocumentType(str, enum.Enum):
    invoice = "invoice"
    spreadsheet_ledger = "spreadsheet_ledger"
    other = "other"
    unknown = "unknown"


class PageKind(str, enum.Enum):
    digital = "digital"
    scanned = "scanned"
    image = "image"
    sheet = "sheet"
    csv = "csv"


class JobStage(str, enum.Enum):
    validate = "validate"
    classify = "classify"
    extract_text = "extract_text"
    ai_extract = "ai_extract"
    validate_financials = "validate_financials"
    embed = "embed"
    reconcile = "reconcile"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"  # retries exhausted; visible in dead-letter view
    cancelled = "cancelled"


class ReviewStatus(str, enum.Enum):
    not_required = "not_required"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Severity(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"


class ResolutionStatus(str, enum.Enum):
    open = "open"
    accepted = "accepted"  # reviewer accepts the discrepancy as-is
    corrected = "corrected"
    dismissed = "dismissed"


class RelationType(str, enum.Enum):
    duplicate_of = "duplicate_of"
    reconciles_with = "reconciles_with"


class MatchStatus(str, enum.Enum):
    proposed = "proposed"
    confirmed = "confirmed"
    rejected = "rejected"
