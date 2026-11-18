"""Glue between the ORM and the pure validation engine.

Extraction (Phase 5) stores two optional hints on `invoice.original_text["_flags"]`:
  {"date_ambiguous": bool, "detected_currencies": ["BDT", ...]}
and source references as dicts carrying a "field" key, e.g. {"field": "total", "page": 3, "text": "..."}.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extraction.normalizers import invoice_key
from app.models import Invoice, ValidationResult
from app.models.base import ResolutionStatus, ReviewStatus, Severity
from app.validation.config import ValidationConfig
from app.validation.engine import validate_invoice
from app.validation.types import ExistingInvoice, Finding, InvoiceData, LineData

_NUMBER_KEY = func.regexp_replace(func.upper(Invoice.invoice_number), "[^A-Z0-9]", "", "g")


def _group_refs(refs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in refs or []:
        grouped[r.get("field", "_")].append(r)
    return dict(grouped)


def invoice_to_data(inv: Invoice) -> InvoiceData:
    flags = (inv.original_text or {}).get("_flags", {})
    return InvoiceData(
        id=str(inv.id), invoice_number=inv.invoice_number, invoice_date=inv.invoice_date,
        supplier_name=inv.supplier_name, customer_name=inv.customer_name, subtotal=inv.subtotal, tax=inv.tax,
        total=inv.total, currency=inv.currency,
        line_items=[
            LineData(li.line_no, li.description, li.quantity, li.unit_price, li.tax, li.total, li.source_references)
            for li in inv.line_items
        ],
        field_refs=_group_refs(inv.source_references),
        detected_currencies=set(flags.get("detected_currencies", [])),
        date_ambiguous=bool(flags.get("date_ambiguous", False)),
    )


def find_existing(session: Session, inv: Invoice) -> list[ExistingInvoice]:
    """Other live invoices sharing this invoice's canonical number (supplier matching happens in the rule)."""
    key = invoice_key(inv.invoice_number)
    if key is None:
        return []
    rows = session.scalars(select(Invoice).where(
        Invoice.id != inv.id, Invoice.deleted_at.is_(None), _NUMBER_KEY == key))
    return [ExistingInvoice(str(r.id), str(r.document_id), r.invoice_number, r.supplier_name, r.invoice_date, r.total)
            for r in rows]


def _key(rule_id: str, field: str | None, expected: str | None, actual: str | None):
    return (rule_id, field, expected, actual)


def run_validation(
    session: Session, invoice_id: uuid.UUID, config: ValidationConfig | None = None, today: date | None = None,
) -> list[Finding]:
    """Validate an invoice and reconcile stored results with the new findings.

    - A finding that still applies keeps its row, so reviewer decisions (accepted/dismissed) survive re-runs.
    - Open findings that no longer apply are removed; resolved ones are kept as history.
    - Reviewer approval is never granted here, and is withdrawn if a new blocking error appears.
    """
    inv = session.get(Invoice, invoice_id)
    if inv is None:
        raise LookupError(f"Invoice {invoice_id} not found")
    findings = validate_invoice(invoice_to_data(inv), config or ValidationConfig.from_settings(),
                                find_existing(session, inv), today)

    stored = {_key(r.rule_id, r.field_name, r.expected_value, r.actual_value): r
              for r in session.scalars(select(ValidationResult).where(ValidationResult.invoice_id == inv.id))}
    seen: set[tuple] = set()
    new_blocking = False
    for f in findings:
        k = _key(f.rule_id, f.field, f.expected, f.actual)
        seen.add(k)
        if k in stored:
            stored[k].description, stored[k].severity = f.description, f.severity
            stored[k].source_references = f.source_references
            continue
        session.add(ValidationResult(
            invoice_id=inv.id, rule_id=f.rule_id, field_name=f.field, severity=f.severity, expected_value=f.expected,
            actual_value=f.actual, description=f.description, source_references=f.source_references))
        new_blocking = new_blocking or f.severity == Severity.error
    for k, row in stored.items():
        if k not in seen and row.resolution_status == ResolutionStatus.open:
            session.delete(row)

    has_error = any(f.severity == Severity.error for f in findings)
    has_warning = any(f.severity == Severity.warning for f in findings)
    status = inv.review_status
    if has_error:
        inv.requires_review = True
        # An existing approval stays valid unless a *new* blocking error appeared since it was given.
        if status == ReviewStatus.not_required or (status == ReviewStatus.approved and new_blocking):
            inv.review_status = ReviewStatus.pending
    elif has_warning and status == ReviewStatus.not_required:
        inv.requires_review, inv.review_status = True, ReviewStatus.pending
    session.flush()
    return findings
