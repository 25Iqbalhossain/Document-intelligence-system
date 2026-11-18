from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_document_for, require_role
from app.core.database import get_db
from app.models import Document, Invoice, ManualCorrection, User, ValidationResult
from app.models.base import ResolutionStatus, ReviewStatus, Severity, UserRole
from app.services.corrections import CorrectionError, VersionConflict, apply_corrections

router = APIRouter(tags=["invoices"])
REVIEWERS = (UserRole.admin, UserRole.reviewer)


class LineOut(BaseModel):
    line_no: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    confidence: dict[str, Any]
    source_references: list[Any]

    model_config = {"from_attributes": True}


class FindingOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    field_name: str | None
    severity: Severity
    expected_value: str | None
    actual_value: str | None
    description: str
    source_references: list[Any]
    resolution_status: ResolutionStatus
    resolution_note: str | None

    model_config = {"from_attributes": True}


class InvoiceOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    row_ref: str | None
    invoice_number: str | None
    invoice_date: date | None
    supplier_name: str | None
    customer_name: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    currency: str | None
    confidence: dict[str, Any]
    source_references: list[Any]
    original_text: dict[str, Any]
    requires_review: bool
    review_status: ReviewStatus
    version: int
    line_items: list[LineOut]
    findings: list[FindingOut]

    @classmethod
    def of(cls, i: Invoice) -> InvoiceOut:
        base = {k: getattr(i, k) for k in (
            "id", "document_id", "row_ref", "invoice_number", "invoice_date", "supplier_name", "customer_name",
            "subtotal", "tax", "total", "currency", "confidence", "source_references", "original_text",
            "requires_review", "review_status", "version")}
        return cls(**base, line_items=[LineOut.model_validate(li) for li in i.line_items],
                   findings=[FindingOut.model_validate(f) for f in i.validation_results])


class CorrectionIn(BaseModel):
    version: int
    reason: str | None = Field(None, max_length=2000)
    fields: dict[str, Any] = Field(default_factory=dict)
    lines: dict[int, dict[str, Any]] = Field(default_factory=dict)


class ReviewIn(BaseModel):
    version: int
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=2000)


class ResolveIn(BaseModel):
    status: Literal["accepted", "dismissed"]
    note: str = Field(min_length=3, max_length=2000)  # a reason is mandatory for audit


def _invoice_for(db: Session, user: User, invoice_id: uuid.UUID) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise HTTPException(404, "Invoice not found")
    get_document_for(db, user, inv.document_id)  # same 404 for foreign documents
    return inv


@router.get("/documents/{document_id}/invoices", response_model=list[InvoiceOut])
def document_invoices(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_document_for(db, user, document_id)
    rows = db.scalars(select(Invoice).where(Invoice.document_id == document_id, Invoice.deleted_at.is_(None))
                      .order_by(Invoice.created_at)).all()
    return [InvoiceOut.of(i) for i in rows]


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return InvoiceOut.of(_invoice_for(db, user, invoice_id))


@router.put("/invoices/{invoice_id}", response_model=InvoiceOut)
def correct_invoice(invoice_id: uuid.UUID, body: CorrectionIn, db: Session = Depends(get_db),
                    user: User = Depends(require_role(*REVIEWERS))):
    _invoice_for(db, user, invoice_id)
    try:
        inv = apply_corrections(db, invoice_id, user_id=user.id, version=body.version, changes=body.fields,
                                line_changes=body.lines, reason=body.reason)
        db.commit()
    except VersionConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except CorrectionError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    db.refresh(inv)
    return InvoiceOut.of(inv)


@router.get("/invoices/{invoice_id}/corrections")
def corrections(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _invoice_for(db, user, invoice_id)
    rows = db.scalars(select(ManualCorrection).where(ManualCorrection.invoice_id == invoice_id)
                      .order_by(ManualCorrection.created_at)).all()
    return [{"at": r.created_at, "field": r.field_name, "old": r.old_value, "new": r.new_value, "reason": r.reason,
             "user_id": r.user_id} for r in rows]


@router.post("/findings/{finding_id}/resolve", response_model=FindingOut)
def resolve_finding(finding_id: uuid.UUID, body: ResolveIn, db: Session = Depends(get_db),
                    user: User = Depends(require_role(*REVIEWERS))):
    f = db.get(ValidationResult, finding_id)
    if f is None:
        raise HTTPException(404, "Finding not found")
    _invoice_for(db, user, f.invoice_id)
    f.resolution_status, f.resolved_by, f.resolved_at = ResolutionStatus(body.status), user.id, datetime.now(UTC)
    f.resolution_note = body.note
    db.commit()
    return FindingOut.model_validate(f)


@router.post("/invoices/{invoice_id}/review", response_model=InvoiceOut)
def review(invoice_id: uuid.UUID, body: ReviewIn, db: Session = Depends(get_db),
           user: User = Depends(require_role(*REVIEWERS))):
    inv = _invoice_for(db, user, invoice_id)
    if inv.version != body.version:
        raise HTTPException(409, f"Invoice changed (current version {inv.version}); reload and retry")
    if body.decision == "approve":
        blocking = [f for f in inv.validation_results
                    if f.severity == Severity.error and f.resolution_status == ResolutionStatus.open]
        if blocking:
            raise HTTPException(409, f"{len(blocking)} unresolved error finding(s): correct the data or "
                                     "accept/dismiss each with a reason before approving")
    inv.review_status = ReviewStatus.approved if body.decision == "approve" else ReviewStatus.rejected
    inv.requires_review = False if body.decision == "approve" else inv.requires_review
    inv.reviewed_by, inv.reviewed_at = user.id, datetime.now(UTC)
    db.commit()
    _close_document_if_reviewed(db, inv.document_id)
    db.refresh(inv)
    return InvoiceOut.of(inv)


def _close_document_if_reviewed(db: Session, document_id: uuid.UUID) -> None:
    """When every invoice of a needs_review document is decided and approved, the document becomes processed."""
    from app.models.base import DocumentStatus

    doc = db.get(Document, document_id)
    if doc is None or doc.status != DocumentStatus.needs_review:
        return
    invs = db.scalars(select(Invoice).where(Invoice.document_id == document_id, Invoice.deleted_at.is_(None))).all()
    if invs and all(i.review_status == ReviewStatus.approved for i in invs):
        doc.status = DocumentStatus.processed
        db.commit()


@router.get("/review/queue", response_model=list[dict])
def review_queue(limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db),
                 user: User = Depends(require_role(*REVIEWERS))):
    rows = db.scalars(select(Invoice).where(Invoice.deleted_at.is_(None), Invoice.requires_review.is_(True),
                                            Invoice.review_status == ReviewStatus.pending)
                      .order_by(Invoice.created_at).limit(limit).offset(offset)).all()
    out = []
    for i in rows:
        open_f = [f for f in i.validation_results if f.resolution_status == ResolutionStatus.open]
        out.append({"invoice_id": i.id, "document_id": i.document_id, "invoice_number": i.invoice_number,
                    "supplier_name": i.supplier_name, "total": i.total,
                    "open_errors": sum(f.severity == Severity.error for f in open_f),
                    "open_warnings": sum(f.severity == Severity.warning for f in open_f)})
    return out
