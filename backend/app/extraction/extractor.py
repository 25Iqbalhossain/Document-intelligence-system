"""Invoice extraction: LLM proposes, deterministic code verifies.

Pipeline per page window: prompt -> JSON -> Pydantic validation (with one repair retry, then an optional
fallback model) -> evidence check -> normalisation -> field-level confidence -> review reasons.
Values that fail the evidence check are NOT stored as field values (the model's proposal is kept in `raw`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.extraction.evidence import APPROX_THRESHOLD, locate_evidence, value_supported
from app.extraction.llm import LLMProvider, LLMResponse, Message
from app.extraction.normalizers import (
    clean_invoice_number,
    detect_currencies,
    normalize_currency,
    parse_date,
    parse_money,
)
from app.extraction.prompts import load_prompt
from app.extraction.schemas import (
    HEADER_FIELDS,
    LINE_FIELDS,
    MONEY_FIELDS,
    ExtractionOut,
    FieldOut,
    InvoiceOut,
)


@dataclass(slots=True)
class PageText:
    number: int
    text: str
    source: str = "native"  # native | ocr
    ocr_confidence: float | None = None  # 0-100


@dataclass(slots=True)
class FieldResult:
    name: str
    raw: str | None  # exactly what the model proposed
    value: str | Decimal | date | None  # normalised; None unless verified and parseable
    evidence: str | None = None
    page: int | None = None
    status: str = "missing"  # verified | approximate | rejected | unparseable | missing
    confidence: float | None = None
    components: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None  # why a proposed value was rejected
    matched_text: str | None = None


@dataclass(slots=True)
class LineResult:
    line_no: int
    fields: dict[str, FieldResult]


@dataclass(slots=True)
class InvoiceResult:
    fields: dict[str, FieldResult]
    lines: list[LineResult]
    raw: dict[str, Any]  # the model's JSON for this invoice, untouched
    window: tuple[int, int]
    review_reasons: list[str] = field(default_factory=list)
    detected_currencies: set[str] = field(default_factory=set)
    date_ambiguous: bool = False


@dataclass(slots=True)
class ExtractionResult:
    invoices: list[InvoiceResult]
    model_name: str | None
    prompt_version: str
    errors: list[str] = field(default_factory=list)  # windows that produced no valid output
    windows_total: int = 0
    used_fallback: bool = False


# ------------------------------------------------------------------ windows & prompting

def make_windows(pages: list[PageText], max_chars: int, max_pages: int) -> list[list[PageText]]:
    windows: list[list[PageText]] = []
    cur: list[PageText] = []
    size = 0
    for p in pages:
        if not p.text.strip():
            continue
        if cur and (size + len(p.text) > max_chars or len(cur) >= max_pages):
            windows.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p.text)
    if cur:
        windows.append(cur)
    return windows


def build_messages(window: list[PageText], prompt_version: str, max_chars: int) -> list[Message]:
    parts = []
    for p in window:
        body = p.text[:max_chars]  # a single page larger than the budget is truncated, not dropped
        body = re.sub(r"</?\s*document", "< document", body, flags=re.I)  # keep the data/instruction boundary intact
        parts.append(f"=== PAGE {p.number} ===\n{body}")
    return [{"role": "system", "content": load_prompt(prompt_version)},
            {"role": "user", "content": "<document>\n" + "\n".join(parts) + "\n</document>"}]


def parse_json_object(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(t[start:end + 1])


def _call_validated(
    provider: LLMProvider, messages: list[Message],
) -> tuple[ExtractionOut | None, LLMResponse | None, str]:
    """Call the model; on malformed output send the error back once and ask for corrected JSON."""
    err, resp = "", None
    for _ in range(2):
        resp = provider.complete(messages)  # transient/permanent transport errors propagate to the job layer
        try:
            return ExtractionOut.model_validate(parse_json_object(resp.text)), resp, ""
        except (ValueError, ValidationError) as exc:
            err = str(exc).replace("\n", " ")[:300]
            messages = [*messages, {"role": "assistant", "content": resp.text[:4000]},
                        {"role": "user", "content": f"Your previous reply was not valid ({err}). "
                                                    "Reply again with ONLY the corrected JSON object."}]
    return None, resp, err


# ------------------------------------------------------------------ field verification

def _normalise(name: str, raw: str) -> tuple[Any, list[str]]:
    """(value, warnings); value None means the text could not be parsed."""
    if name in MONEY_FIELDS or name == "quantity":
        m = parse_money(raw)
        return m.value, list(m.warnings)
    if name == "invoice_date":
        d = parse_date(raw)
        return d.value, list(d.warnings)
    if name == "currency":
        code = normalize_currency(raw)
        if code is None and re.fullmatch(r"[A-Za-z]{3}", raw.strip()):
            code = raw.strip().upper()  # unknown but well-formed code: keep, validation will flag it
        return code, []
    if name == "invoice_number":
        return clean_invoice_number(raw), []
    return re.sub(r"\s+", " ", raw).strip() or None, []


def check_field(name: str, fo: FieldOut, pages: dict[int, PageText]) -> FieldResult:
    if fo.value is None:
        return FieldResult(name, None, None, status="missing")
    res = FieldResult(name, fo.value, None, evidence=fo.evidence, page=fo.page)
    if not fo.evidence:
        res.status, res.reason, res.confidence = "rejected", "no_evidence", 0.0
        return res
    loc = locate_evidence(fo.evidence, {n: p.text for n, p in pages.items()}, fo.page)
    if loc.page is None or loc.score < APPROX_THRESHOLD:
        res.status, res.reason, res.confidence = "rejected", "evidence_not_found", 0.0
        return res
    source_quote = fo.evidence if loc.exact else (loc.matched_text or fo.evidence)
    if not value_supported(name, fo.value, source_quote):
        res.status, res.reason, res.confidence = "rejected", "value_not_in_evidence", 0.0
        return res
    value, warnings = _normalise(name, fo.value)
    res.page, res.matched_text, res.warnings = loc.page, loc.matched_text, warnings
    if loc.page_corrected:
        res.warnings.append("page_corrected")
    if value is None:
        res.status, res.reason, res.confidence = "unparseable", "unparseable_value", 0.0
        return res
    page = pages[loc.page]
    ocr = min(page.ocr_confidence / 100, 1.0) if page.source == "ocr" and page.ocr_confidence is not None else 1.0
    # page_corrected is informational; day/month ambiguity is penalised once, in build_invoice
    parse = 0.85 if any(w not in ("page_corrected", "ambiguous_day_month") for w in warnings) else 1.0
    res.components = {"evidence": round(loc.score, 3), "ocr": round(ocr, 3), "parse": parse}
    res.confidence = round(loc.score * ocr * parse, 3)
    res.value, res.status = value, ("verified" if loc.exact else "approximate")
    return res


def _reference(r: FieldResult, label: str) -> dict[str, Any]:
    return {"field": label, "page": r.page, "text": r.matched_text or r.evidence,
            "match": r.status, "score": r.components.get("evidence")}


def _review_reasons(label: str, r: FieldResult, min_conf: float) -> list[str]:
    if r.status == "rejected":
        return [f"field_rejected:{label}:{r.reason}"]
    if r.status == "unparseable":
        return [f"field_unparseable:{label}"]
    out = []
    if r.status == "approximate":
        out.append(f"approximate_evidence:{label}")
    if r.confidence is not None and r.confidence < min_conf:
        out.append(f"low_confidence:{label}")
    return out


def build_invoice(out: InvoiceOut, window: list[PageText], settings: Settings) -> InvoiceResult:
    pages = {p.number: p for p in window}
    fields = {n: check_field(n, getattr(out, n), pages) for n in HEADER_FIELDS}
    lines: list[LineResult] = []
    for item in out.line_items:
        lf = {n: check_field(n, getattr(item, n), pages) for n in LINE_FIELDS}
        if all(f.status == "missing" for f in lf.values()):
            continue  # an entirely empty row carries no information
        lines.append(LineResult(len(lines) + 1, lf))

    reasons: list[str] = []
    for n, r in fields.items():
        reasons += _review_reasons(n, r, settings.extraction_min_confidence)
    for li in lines:
        for n, r in li.fields.items():
            reasons += _review_reasons(f"line_items[{li.line_no}].{n}", r, settings.extraction_min_confidence)
    date_ambiguous = False
    d = fields["invoice_date"]
    if d.status in ("verified", "approximate") and d.raw:
        date_ambiguous = parse_date(d.raw).ambiguous
        if date_ambiguous:
            d.confidence = round((d.confidence or 0) * 0.6, 3)
            d.components["ambiguity"] = 0.6
            reasons.append("ambiguous_date")
            reasons = [x for x in reasons if x != "low_confidence:invoice_date"] + (
                ["low_confidence:invoice_date"] if d.confidence < settings.extraction_min_confidence else [])
    if not any(f.status in ("verified", "approximate") for f in fields.values()):
        reasons.append("nothing_verified")

    ref_pages = {r.page for r in fields.values() if r.page}
    detected: set[str] = set()
    for p in ref_pages:
        detected |= detect_currencies(pages[p].text)
    return InvoiceResult(fields, lines, out.model_dump(mode="json"), (window[0].number, window[-1].number),
                         list(dict.fromkeys(reasons)), detected, date_ambiguous)


# ------------------------------------------------------------------ entry point

def extract_invoices(
    pages: list[PageText], provider: LLMProvider, *, fallback: LLMProvider | None = None,
    settings: Settings | None = None,
) -> ExtractionResult:
    s = settings or get_settings()
    windows = make_windows(pages, s.extraction_max_chars, s.extraction_window_pages)
    result = ExtractionResult([], None, s.prompt_version, windows_total=len(windows))
    for window in windows:
        messages = build_messages(window, s.prompt_version, s.extraction_max_chars)
        label = f"pages {window[0].number}-{window[-1].number}"
        out, resp, err = _call_validated(provider, messages)
        if out is None and fallback is not None:
            out, resp, err2 = _call_validated(fallback, messages)
            result.used_fallback = result.used_fallback or out is not None
            err = err2 or err
        if out is None:
            result.errors.append(f"{label}: model output invalid after retry ({err})")
            continue
        result.model_name = result.model_name or (resp.model if resp else None)
        result.invoices += [build_invoice(inv, window, s) for inv in out.invoices]
    return result
