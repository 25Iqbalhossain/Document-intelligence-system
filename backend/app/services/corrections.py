"""Human corrections: audited, optimistic-locked, always followed by re-validation."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import Invoice, ManualCorrection
from app.models.base import ReviewStatus
from app.services.validation_service import run_validation

INVOICE_FIELDS = {
    "invoice_number": "str", "supplier_name": "str", "customer_name": "str", "currency": "str",
    "invoice_date": "date", "subtotal": "money", "tax": "money", "total": "money",
}
LINE_FIELDS = {"description": "str", "quantity": "qty", "unit_price": "money", "tax": "money", "total": "money"}


class CorrectionError(Exception):
    pass


class VersionConflict(CorrectionError):
    pass


def _parse(kind: str, name: str, raw: Any) -> Any:
    """None means 'not present in the source' and is kept as NULL. An empty string is not a value."""
    if raw is None:
        return None
    try:
        if kind == "str":
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("empty text; send null to clear the field")
            return raw.strip()
        if kind == "date":
            return date.fromisoformat(str(raw))
        if isinstance(raw, float):
            raise ValueError("send numbers as strings to avoid binary rounding")
        d = Decimal(str(raw).strip())
        if not d.is_finite():
            raise ValueError("not a finite number")
        return d
    except (ValueError, InvalidOperation) as exc:
        raise CorrectionError(f"{name}: {exc}") from None


def _show(v: Any) -> str | None:
    return None if v is None else str(v)


def apply_corrections(
    session: Session, invoice_id: uuid.UUID, *, user_id: uuid.UUID, version: int, changes: dict[str, Any],
    line_changes: dict[int, dict[str, Any]] | None = None, reason: str | None = None,
) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise CorrectionError("Invoice not found")
    if inv.version != version:
        raise VersionConflict(f"Invoice changed (current version {inv.version}); reload and retry")
    unknown = set(changes) - set(INVOICE_FIELDS)
    if unknown:
        raise CorrectionError(f"Unknown fields: {sorted(unknown)}")
    n = 0
    for name, raw in changes.items():
        new = _parse(INVOICE_FIELDS[name], name, raw)
        old = getattr(inv, name)
        if name == "currency" and new:
            new = new.upper()
        if old == new:
            continue
        session.add(ManualCorrection(invoice_id=inv.id, field_name=name, old_value=_show(old),
                                     new_value=_show(new), reason=reason, user_id=user_id))
        setattr(inv, name, new)
        n += 1
    by_no = {li.line_no: li for li in inv.line_items}
    for line_no, fields in (line_changes or {}).items():
        li = by_no.get(line_no)
        if li is None:
            raise CorrectionError(f"Line {line_no} does not exist")
        bad = set(fields) - set(LINE_FIELDS)
        if bad:
            raise CorrectionError(f"Unknown line fields: {sorted(bad)}")
        for name, raw in fields.items():
            new = _parse(LINE_FIELDS[name], f"line {line_no} {name}", raw)
            old = getattr(li, name)
            if old == new:
                continue
            session.add(ManualCorrection(
                invoice_id=inv.id, line_item_id=li.id, field_name=f"line_items[{line_no}].{name}",
                old_value=_show(old), new_value=_show(new), reason=reason, user_id=user_id))
            setattr(li, name, new)
            n += 1
    if n == 0:
        return inv
    # A correction invalidates any earlier approval: a human must look at the new state.
    inv.review_status, inv.reviewed_by, inv.reviewed_at = ReviewStatus.pending, None, None
    inv.requires_review = True
    try:
        session.flush()
    except StaleDataError:
        raise VersionConflict("Invoice was modified concurrently; reload and retry") from None
    run_validation(session, inv.id)
    return inv
