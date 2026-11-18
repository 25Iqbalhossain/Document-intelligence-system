"""Field-by-field comparison of two invoice records (any source: PDF, image, Excel, CSV).

Pure functions on `InvoiceData`. Every discrepancy carries both values and the source reference of each side.
Comparison never changes either record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from app.extraction.normalizers import invoice_key, name_key
from app.models.base import Severity
from app.validation.config import ValidationConfig
from app.validation.types import InvoiceData, LineData

NAME_VARIANT_RATIO = 0.9  # names this similar are reported as a variant (warning), not a conflict (error)


@dataclass(frozen=True, slots=True)
class Discrepancy:
    field: str
    kind: str  # value_mismatch | missing_in_left | missing_in_right | format_difference | name_variant | info
    severity: Severity
    left: str | None
    right: str | None
    description: str
    left_ref: dict[str, Any] | None = None
    right_ref: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


def _ref(inv: InvoiceData, fld: str) -> dict[str, Any] | None:
    refs = inv.field_refs.get(fld)
    return refs[0] if refs else None


def _missing(fld: str, left: Any, right: Any, lhs: InvoiceData, rhs: InvoiceData, fmt) -> Discrepancy | None:
    if left is None and right is not None:
        return Discrepancy(fld, "missing_in_left", Severity.warning, None, fmt(right),
                           f"{fld} is present only on the right side", None, _ref(rhs, fld))
    if right is None and left is not None:
        return Discrepancy(fld, "missing_in_right", Severity.warning, fmt(left), None,
                           f"{fld} is present only on the left side", _ref(lhs, fld), None)
    return None


def _compare_money(fld: str, lhs: InvoiceData, rhs: InvoiceData, cfg: ValidationConfig) -> list[Discrepancy]:
    a, b = getattr(lhs, fld), getattr(rhs, fld)
    if a is None and b is None:
        return []
    if a is None or b is None:
        return [d for d in [_missing(fld, a, b, lhs, rhs, cfg.fmt)] if d]
    if cfg.within(a, b):
        return []
    return [Discrepancy(fld, "value_mismatch", Severity.error, cfg.fmt(a), cfg.fmt(b),
                        f"{fld} differs by {cfg.fmt(abs(a - b))}", _ref(lhs, fld), _ref(rhs, fld))]


def _compare_name(fld: str, lhs: InvoiceData, rhs: InvoiceData) -> list[Discrepancy]:
    a, b = getattr(lhs, fld), getattr(rhs, fld)
    if a is None and b is None:
        return []
    if a is None or b is None:
        return [d for d in [_missing(fld, a, b, lhs, rhs, str)] if d]
    ka, kb = name_key(a), name_key(b)
    if ka == kb:
        return []
    if ka and kb and SequenceMatcher(None, ka, kb).ratio() >= NAME_VARIANT_RATIO:
        return [Discrepancy(fld, "name_variant", Severity.warning, a, b, f"{fld} spelled differently",
                            _ref(lhs, fld), _ref(rhs, fld))]
    return [Discrepancy(fld, "value_mismatch", Severity.error, a, b, f"{fld} differs", _ref(lhs, fld), _ref(rhs, fld))]


def _compare_scalar(fld: str, lhs: InvoiceData, rhs: InvoiceData, norm=lambda v: v, fmt=str) -> list[Discrepancy]:
    a, b = getattr(lhs, fld), getattr(rhs, fld)
    if a is None and b is None:
        return []
    if a is None or b is None:
        return [d for d in [_missing(fld, a, b, lhs, rhs, fmt)] if d]
    if norm(a) == norm(b):
        return []
    return [Discrepancy(fld, "value_mismatch", Severity.error, fmt(a), fmt(b), f"{fld} differs",
                        _ref(lhs, fld), _ref(rhs, fld))]


def _compare_number(lhs: InvoiceData, rhs: InvoiceData) -> list[Discrepancy]:
    a, b = lhs.invoice_number, rhs.invoice_number
    if a is None and b is None:
        return []
    if a is None or b is None:
        return [d for d in [_missing("invoice_number", a, b, lhs, rhs, str)] if d]
    if a == b:
        return []
    if invoice_key(a) == invoice_key(b):
        return [Discrepancy("invoice_number", "format_difference", Severity.info, a, b,
                            "Same invoice number written differently", _ref(lhs, "invoice_number"),
                            _ref(rhs, "invoice_number"))]
    return [Discrepancy("invoice_number", "value_mismatch", Severity.error, a, b, "invoice_number differs",
                        _ref(lhs, "invoice_number"), _ref(rhs, "invoice_number"))]


def _desc_key(li: LineData) -> str | None:
    return " ".join(li.description.casefold().split()) if li.description and li.description.strip() else None


def _line_label(li: LineData) -> str:
    return f"line_items[{li.description or li.line_no}]"


def _compare_line(a: LineData, b: LineData, label: str, cfg: ValidationConfig) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    ref_a = a.refs[0] if a.refs else None
    ref_b = b.refs[0] if b.refs else None
    pairs: list[tuple[str, Decimal | None, Decimal | None, bool]] = [
        ("quantity", a.quantity, b.quantity, False), ("unit_price", a.unit_price, b.unit_price, True),
        ("tax", a.tax, b.tax, True), ("total", a.total, b.total, True),
    ]
    for name, x, y, money in pairs:
        if x is None or y is None:
            if (x is None) != (y is None):
                out.append(Discrepancy(f"{label}.{name}", "missing_in_left" if x is None else "missing_in_right",
                                       Severity.warning, None if x is None else str(x), None if y is None else str(y),
                                       f"{name} present on one side only", ref_a, ref_b))
            continue
        same = cfg.within(x, y) if money else x == y
        if not same:
            out.append(Discrepancy(f"{label}.{name}", "value_mismatch", Severity.error,
                                   cfg.fmt(x) if money else str(x), cfg.fmt(y) if money else str(y),
                                   f"line {name} differs", ref_a, ref_b))
    return out


def _compare_lines(lhs: InvoiceData, rhs: InvoiceData, cfg: ValidationConfig) -> list[Discrepancy]:
    a, b = lhs.line_items, rhs.line_items
    if not a and not b:
        return []
    if not a or not b:  # one side has no line detail: say so instead of silently skipping
        side = "left" if not a else "right"
        return [Discrepancy("line_items", "info", Severity.info, str(len(a)), str(len(b)),
                            f"Line items not compared: the {side} side has none")]
    ka, kb = [_desc_key(x) for x in a], [_desc_key(x) for x in b]
    by_desc = all(ka) and all(kb) and len(set(ka)) == len(ka) and len(set(kb)) == len(kb)
    out: list[Discrepancy] = []
    if by_desc:
        index_b = {k: x for k, x in zip(kb, b, strict=True)}
        seen: set[str] = set()
        for key, x in zip(ka, a, strict=True):
            y = index_b.get(key)
            if y is None:
                out.append(Discrepancy(_line_label(x), "missing_in_right", Severity.warning, x.description, None,
                                       "Line exists only on the left side", x.refs[0] if x.refs else None))
                continue
            seen.add(key)
            out += _compare_line(x, y, _line_label(x), cfg)
        for key, y in index_b.items():
            if key not in seen and key not in set(ka):
                out.append(Discrepancy(_line_label(y), "missing_in_left", Severity.warning, None, y.description,
                                       "Line exists only on the right side", None, y.refs[0] if y.refs else None))
        return out
    if len(a) != len(b):
        return [Discrepancy("line_items", "value_mismatch", Severity.error, str(len(a)), str(len(b)),
                            "Different number of line items and they cannot be paired by description")]
    for x, y in zip(a, b, strict=True):  # same count, unpairable descriptions: compare by position
        out += _compare_line(x, y, f"line_items[{x.line_no}]", cfg)
    return out


def compare_records(left: InvoiceData, right: InvoiceData, cfg: ValidationConfig | None = None) -> list[Discrepancy]:
    cfg = cfg or ValidationConfig()
    out = _compare_number(left, right)
    out += _compare_scalar("invoice_date", left, right, fmt=lambda d: d.isoformat())
    out += _compare_name("supplier_name", left, right)
    out += _compare_name("customer_name", left, right)
    for fld in ("subtotal", "tax", "total"):
        out += _compare_money(fld, left, right, cfg)
    out += _compare_scalar("currency", left, right, norm=lambda c: c.strip().upper())
    out += _compare_lines(left, right, cfg)
    return out
