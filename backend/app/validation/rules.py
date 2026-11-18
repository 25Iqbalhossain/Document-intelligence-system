"""Deterministic validation rules. Every rule is a pure function: (invoice, config, context) -> findings.

All money arithmetic uses Decimal. Nothing here calls an LLM. When a check cannot be performed
because inputs are missing, an `info` finding says so — checks are never skipped silently.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.extraction.normalizers import KNOWN_CURRENCIES, invoice_key, name_key
from app.models.base import Severity
from app.validation.config import ValidationConfig
from app.validation.types import ExistingInvoice, Finding, InvoiceData

E, W, I = Severity.error, Severity.warning, Severity.info  # noqa: E741


@dataclass(frozen=True, slots=True)
class RuleContext:
    today: date
    existing: tuple[ExistingInvoice, ...] = ()


Rule = Callable[[InvoiceData, ValidationConfig, RuleContext], list[Finding]]


def _refs(inv: InvoiceData, *fields: str) -> list[dict]:
    out: list[dict] = []
    for f in fields:
        out.extend(inv.field_refs.get(f, []))
    return out


def _missing(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# 1 ------------------------------------------------------------------
def required_fields(inv, cfg, ctx):
    return [
        Finding("required_field_missing", E, f"Required field '{f}' was not found in the document",
                field=f, expected="present", actual=None, source_references=_refs(inv, f))
        for f in cfg.required_fields if _missing(getattr(inv, f, None))
    ]


# 2 ------------------------------------------------------------------
def invoice_date_valid(inv, cfg, ctx):
    out: list[Finding] = []
    d = inv.invoice_date
    if d is not None:
        latest = ctx.today + timedelta(days=cfg.max_future_days)
        if d < cfg.min_date or d > latest:
            out.append(Finding("invalid_invoice_date", E,
                               f"Invoice date {d.isoformat()} is outside the plausible range "
                               f"{cfg.min_date.isoformat()}..{latest.isoformat()}",
                               field="invoice_date", expected=f"{cfg.min_date}..{latest}", actual=d.isoformat(),
                               source_references=_refs(inv, "invoice_date")))
    if inv.date_ambiguous:
        out.append(Finding("ambiguous_invoice_date", W,
                           "Invoice date could be read as day/month or month/day; confirm the intended reading",
                           field="invoice_date", actual=d.isoformat() if d else None,
                           source_references=_refs(inv, "invoice_date")))
    return out


# 3 ------------------------------------------------------------------
def duplicate_invoice(inv, cfg, ctx):
    """Flags (never deletes). Same number+supplier is a duplicate; differing values make it a conflict."""
    key, supplier = invoice_key(inv.invoice_number), name_key(inv.supplier_name)
    if key is None or supplier is None:
        return []
    others = [e for e in ctx.existing if e.id != inv.id and invoice_key(e.invoice_number) == key
              and name_key(e.supplier_name) == supplier]
    if not others:
        return []
    ids = ", ".join(sorted(e.id for e in others))
    identical = [
        e for e in others
        if e.invoice_date == inv.invoice_date and inv.total is not None and e.total is not None
        and cfg.within(inv.total, e.total)
    ]
    if identical:
        return [Finding("duplicate_invoice", E,
                        f"Invoice {inv.invoice_number} from {inv.supplier_name} already exists with the same date "
                        f"and total ({len(others)} existing record{'s' if len(others) != 1 else ''}). "
                        "Review before keeping both.",
                        field="invoice_number", expected="unique invoice", actual=f"duplicate of {ids}",
                        source_references=_refs(inv, "invoice_number"))]
    diffs = sorted({f"date {e.invoice_date}, total {cfg.fmt(e.total)}" for e in others})
    return [Finding("duplicate_invoice_number_conflict", E,
                    f"Invoice number {inv.invoice_number} from {inv.supplier_name} already exists with different "
                    f"values ({'; '.join(diffs)}; this one: date {inv.invoice_date}, total {cfg.fmt(inv.total)})",
                    field="invoice_number", expected="unique invoice number per supplier",
                    actual=f"conflicts with {ids}",
                    source_references=_refs(inv, "invoice_number", "total"))]


# 4 ------------------------------------------------------------------
def subtotal_matches_lines(inv, cfg, ctx):
    if inv.subtotal is None:
        return []
    if not inv.line_items:
        return [Finding("subtotal_unchecked", I, "Subtotal not verified: no line items were extracted",
                        field="subtotal", actual=cfg.fmt(inv.subtotal))]
    missing = [li.line_no for li in inv.line_items if li.total is None]
    if missing:
        return [Finding("subtotal_unchecked", I,
                        f"Subtotal not verified: line total missing on line(s) {missing}",
                        field="subtotal", actual=cfg.fmt(inv.subtotal))]
    n = len(inv.line_items)
    gross = sum((li.total for li in inv.line_items), Decimal(0))
    candidates = [gross]
    if all(li.tax is not None for li in inv.line_items):  # line totals may be tax-inclusive
        candidates.append(gross - sum((li.tax for li in inv.line_items), Decimal(0)))
    if any(cfg.within(c, inv.subtotal, n) for c in candidates):
        return []
    return [Finding("subtotal_mismatch", E,
                    f"Subtotal {cfg.fmt(inv.subtotal)} does not equal the sum of {n} line totals ({cfg.fmt(gross)})",
                    field="subtotal", expected=cfg.fmt(gross), actual=cfg.fmt(inv.subtotal),
                    source_references=_refs(inv, "subtotal") + [r for li in inv.line_items for r in li.refs])]


# 5 ------------------------------------------------------------------
def line_totals_match(inv, cfg, ctx):
    out: list[Finding] = []
    unchecked = 0
    for li in inv.line_items:
        if li.quantity is None or li.unit_price is None or li.total is None:
            unchecked += 1
            continue
        base = li.quantity * li.unit_price
        candidates = [base] + ([base + li.tax] if li.tax is not None else [])
        if not any(cfg.within(c, li.total) for c in candidates):
            out.append(Finding("line_total_mismatch", E,
                               f"Line {li.line_no}: quantity {li.quantity} x unit price {cfg.fmt(li.unit_price)} = "
                               f"{cfg.fmt(base)}, but the line total is {cfg.fmt(li.total)}",
                               field=f"line_items[{li.line_no}].total", expected=cfg.fmt(base),
                               actual=cfg.fmt(li.total), source_references=li.refs))
    if unchecked:
        out.append(Finding("line_totals_unchecked", I,
                           f"{unchecked} line(s) lack quantity, unit price or total and could not be verified"))
    return out


# 6 ------------------------------------------------------------------
def tax_matches_lines(inv, cfg, ctx):
    out: list[Finding] = []
    if inv.tax is None:
        return out
    if inv.line_items and all(li.tax is not None for li in inv.line_items):
        expected = sum((li.tax for li in inv.line_items), Decimal(0))
        if not cfg.within(expected, inv.tax, len(inv.line_items)):
            out.append(Finding("tax_mismatch", E,
                               f"Invoice tax {cfg.fmt(inv.tax)} does not equal the sum of line taxes "
                               f"({cfg.fmt(expected)})",
                               field="tax", expected=cfg.fmt(expected), actual=cfg.fmt(inv.tax),
                               source_references=_refs(inv, "tax")))
    if inv.subtotal is not None and inv.subtotal > 0 and inv.tax > inv.subtotal:
        out.append(Finding("tax_exceeds_subtotal", W, "Tax is larger than the subtotal", field="tax",
                           expected=f"<= {cfg.fmt(inv.subtotal)}", actual=cfg.fmt(inv.tax),
                           source_references=_refs(inv, "tax", "subtotal")))
    return out


# 7 ------------------------------------------------------------------
def total_matches(inv, cfg, ctx):
    if inv.total is None:
        return []
    if inv.subtotal is None:
        return [Finding("total_unchecked", I, "Total not verified: subtotal missing", field="total",
                        actual=cfg.fmt(inv.total))]
    if inv.tax is None:
        if cfg.within(inv.subtotal, inv.total):
            return []
        return [Finding("total_unverifiable_tax_missing", W,
                        "Total differs from subtotal but no tax amount was extracted to explain the difference",
                        field="total", expected=cfg.fmt(inv.subtotal), actual=cfg.fmt(inv.total),
                        source_references=_refs(inv, "total", "subtotal"))]
    expected = inv.subtotal + inv.tax
    if cfg.within(expected, inv.total):
        return []
    return [Finding("total_mismatch", E,
                    f"Total {cfg.fmt(inv.total)} does not equal subtotal {cfg.fmt(inv.subtotal)} + tax "
                    f"{cfg.fmt(inv.tax)} = {cfg.fmt(expected)}",
                    field="total", expected=cfg.fmt(expected), actual=cfg.fmt(inv.total),
                    source_references=_refs(inv, "total", "subtotal", "tax"))]


# 8 ------------------------------------------------------------------
def currency_consistent(inv, cfg, ctx):
    out: list[Finding] = []
    has_amounts = any(v is not None for v in (inv.subtotal, inv.tax, inv.total))
    cur = inv.currency.strip().upper() if inv.currency else None
    if cur is None:
        if has_amounts:
            out.append(Finding("currency_missing", W, "Amounts are present but no currency was identified",
                               field="currency", expected="ISO 4217 code", actual=None))
    elif not re.fullmatch(r"[A-Z]{3}", cur):
        out.append(Finding("currency_invalid", E, f"'{inv.currency}' is not a 3-letter currency code",
                           field="currency", expected="ISO 4217 code", actual=inv.currency,
                           source_references=_refs(inv, "currency")))
    elif cur not in KNOWN_CURRENCIES:
        out.append(Finding("currency_unrecognized", W, f"Currency {cur} is not in the supported list; confirm it",
                           field="currency", actual=cur, source_references=_refs(inv, "currency")))
    seen = {c.upper() for c in inv.detected_currencies}
    if len(seen) > 1:
        out.append(Finding("currency_mixed", W, f"Multiple currencies appear in the document: {sorted(seen)}",
                           field="currency", expected="a single currency", actual=", ".join(sorted(seen))))
    elif cur and seen and cur not in seen:
        out.append(Finding("currency_conflict", W,
                           f"Extracted currency {cur} differs from the currency seen in the text ({sorted(seen)[0]})",
                           field="currency", expected=sorted(seen)[0], actual=cur))
    return out


# 9 ------------------------------------------------------------------
def numbers_valid(inv, cfg, ctx):
    out: list[Finding] = []
    named: list[tuple[str, Decimal | None, list[dict]]] = [
        ("subtotal", inv.subtotal, _refs(inv, "subtotal")), ("tax", inv.tax, _refs(inv, "tax")),
        ("total", inv.total, _refs(inv, "total")),
    ]
    for li in inv.line_items:
        p = f"line_items[{li.line_no}]"
        named += [(f"{p}.quantity", li.quantity, li.refs), (f"{p}.unit_price", li.unit_price, li.refs),
                  (f"{p}.tax", li.tax, li.refs), (f"{p}.total", li.total, li.refs)]
    for name, v, refs in named:
        if v is None:
            continue
        if not v.is_finite():
            out.append(Finding("invalid_number", E, f"{name} is not a finite number", field=name, actual=str(v),
                               source_references=refs))
        elif abs(v) > cfg.max_amount:
            out.append(Finding("implausible_amount", W, f"{name} is implausibly large", field=name,
                               expected=f"<= {cfg.max_amount}", actual=str(v), source_references=refs))
        elif v < 0:
            out.append(Finding("negative_amount", cfg.negative_severity,
                               f"{name} is negative (credit note or extraction error?)", field=name,
                               expected=">= 0", actual=str(v), source_references=refs))
        elif v == 0 and name.endswith("quantity"):
            out.append(Finding("zero_quantity", W, f"{name} is zero", field=name, expected="> 0", actual="0",
                               source_references=refs))
    return out


RULES: tuple[tuple[str, Rule], ...] = (
    ("required_fields", required_fields),
    ("invoice_date_valid", invoice_date_valid),
    ("duplicate_invoice", duplicate_invoice),
    ("subtotal_matches_lines", subtotal_matches_lines),
    ("line_totals_match", line_totals_match),
    ("tax_matches_lines", tax_matches_lines),
    ("total_matches", total_matches),
    ("currency_consistent", currency_consistent),
    ("numbers_valid", numbers_valid),
)
