"""Runs extraction for a document and persists invoices with full traceability.

Text documents: classify -> LLM extraction (evidence-verified). Spreadsheets/CSV: deterministic column mapping.
Re-running replaces *unreviewed* invoices only; once a human has reviewed or corrected anything, a re-run
refuses (ExtractionConflict) instead of destroying their work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.extraction.classifier import classify_text
from app.extraction.extractor import (
    ExtractionResult,
    FieldResult,
    InvoiceResult,
    PageText,
    extract_invoices,
)
from app.extraction.llm import LLMProvider, get_provider
from app.extraction.schemas import HEADER_FIELDS, LINE_FIELDS
from app.extraction.tabular import map_table
from app.models import Document, DocumentPage, ExtractedText, Invoice, InvoiceLineItem, ManualCorrection
from app.models.base import DocumentType, PageKind, ReviewStatus
from app.services import orchestration as orch

MONEY_LIMIT = Decimal(10) ** 14  # NUMERIC(18,4) holds at most 14 integer digits
QTY_LIMIT = Decimal(10) ** 12  # NUMERIC(18,6)
_STR_LIMITS = {"invoice_number": 128, "supplier_name": 512, "customer_name": 512, "currency": 8}


class ExtractionConflict(Exception):
    """Reviewed or corrected invoices exist; re-extraction would overwrite human work."""


@dataclass(slots=True)
class ExtractionSummary:
    doc_type: DocumentType
    invoice_ids: list[uuid.UUID] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    used_fallback: bool = False


def _confidence(fr: FieldResult) -> dict[str, Any]:
    return {"score": fr.confidence, "status": fr.status, **fr.components, "warnings": fr.warnings}


def _ref(label: str, fr: FieldResult) -> dict[str, Any]:
    return {"field": label, "page": fr.page, "text": fr.matched_text or fr.evidence, "match": fr.status,
            "score": fr.components.get("evidence")}


def _guard(label: str, value: Any, reasons: list[str]) -> Any:
    """Keep values the database can hold; anything else is dropped with a visible review reason."""
    if isinstance(value, Decimal):
        limit = QTY_LIMIT if label.endswith("quantity") else MONEY_LIMIT
        if abs(value) >= limit:
            reasons.append(f"value_out_of_range:{label}")
            return None
    base = label.rsplit(".", 1)[-1]
    if isinstance(value, str) and base in _STR_LIMITS and len(value) > _STR_LIMITS[base]:
        reasons.append(f"value_too_long:{label}")
        return None
    return value


def _build_invoice(doc: Document, r: InvoiceResult, kind: str, model: str | None, prompt: str | None) -> Invoice:
    reasons = list(r.review_reasons)
    vals = {n: _guard(n, r.fields[n].value, reasons) for n in HEADER_FIELDS}
    refs = [_ref(n, f) for n, f in r.fields.items() if f.status in ("verified", "approximate")]
    inv = Invoice(
        document_id=doc.id, source_kind=kind,
        row_ref=r.raw.get("row_ref") if kind == "spreadsheet" else f"pages {r.window[0]}-{r.window[1]}",
        invoice_number=vals["invoice_number"], invoice_date=vals["invoice_date"],
        supplier_name=vals["supplier_name"], customer_name=vals["customer_name"], subtotal=vals["subtotal"],
        tax=vals["tax"], total=vals["total"], currency=vals["currency"],
        confidence={n: _confidence(f) for n, f in r.fields.items()},
        source_references=refs, raw_extraction=r.raw, model_name=model, prompt_version=prompt,
    )
    inv.original_text = {n: f.raw for n, f in r.fields.items() if f.raw is not None}
    for li in r.lines:
        lvals = {n: _guard(f"line_items[{li.line_no}].{n}", li.fields[n].value, reasons) for n in LINE_FIELDS}
        lrefs = [_ref(f"line_items[{li.line_no}].{n}", f) for n, f in li.fields.items()
                 if f.status in ("verified", "approximate")]
        inv.line_items.append(InvoiceLineItem(
            line_no=li.line_no, description=lvals["description"], quantity=lvals["quantity"],
            unit_price=lvals["unit_price"], tax=lvals["tax"], total=lvals["total"],
            confidence={n: _confidence(f) for n, f in li.fields.items()}, source_references=lrefs,
            original_text={n: f.raw for n, f in li.fields.items() if f.raw is not None},
        ))
    reasons = list(dict.fromkeys(reasons))
    inv.original_text["_flags"] = {
        "date_ambiguous": r.date_ambiguous, "detected_currencies": sorted(r.detected_currencies),
        "review_reasons": reasons, "window": list(r.window),
    }
    inv.requires_review = bool(reasons)
    inv.review_status = ReviewStatus.pending if reasons else ReviewStatus.not_required
    return inv


def _load_text_pages(session: Session, document_id: uuid.UUID) -> list[PageText]:
    conf = {p.page_number: p.ocr_confidence for p in session.scalars(
        select(DocumentPage).where(DocumentPage.document_id == document_id))}
    rows = session.scalars(select(ExtractedText).where(ExtractedText.document_id == document_id)
                           .order_by(ExtractedText.page_number, ExtractedText.source))
    by_page: dict[int, PageText] = {}
    for r in rows:
        ocr = r.ocr_confidence if r.ocr_confidence is not None else conf.get(r.page_number)
        by_page.setdefault(r.page_number, PageText(r.page_number, r.text, r.source, ocr))
    return [by_page[k] for k in sorted(by_page)]


def _replace_unreviewed(session: Session, document_id: uuid.UUID, force: bool) -> None:
    existing = session.scalars(select(Invoice).where(Invoice.document_id == document_id)).all()
    if not existing:
        return
    ids = [i.id for i in existing]
    corrected = session.scalar(select(ManualCorrection.id).where(ManualCorrection.invoice_id.in_(ids)).limit(1))
    reviewed = any(i.review_status in (ReviewStatus.approved, ReviewStatus.rejected) for i in existing)
    if (corrected or reviewed) and not force:
        raise ExtractionConflict("Invoices for this document were already reviewed or corrected; "
                                 "re-extraction would overwrite that work")
    for inv in existing:
        session.delete(inv)
    session.flush()


def run_extraction(
    session: Session, document_id: uuid.UUID, *, provider: LLMProvider | None = None,
    fallback: LLMProvider | None = None, settings: Settings | None = None, force: bool = False,
) -> ExtractionSummary:
    s = settings or get_settings()
    doc = session.get(Document, document_id)
    if doc is None:
        raise LookupError(f"Document {document_id} not found")
    pages = list(session.scalars(select(DocumentPage).where(DocumentPage.document_id == document_id)
                                 .order_by(DocumentPage.page_number)))
    tabular = bool(pages) and all(p.kind in (PageKind.sheet, PageKind.csv) for p in pages)

    result: ExtractionResult | None = None
    invoice_results: list[InvoiceResult] = []
    summary: ExtractionSummary
    if tabular:
        skipped = []
        for p in pages:
            for table in (p.metadata_ or {}).get("tables", []):
                found, why = map_table(table, p.page_number)
                invoice_results += found
                if why:
                    skipped.append({"sheet": p.label, "reason": why})
        doc.metadata_ = {**(doc.metadata_ or {}), "tabular_skipped": skipped}  # never silent
        doc.doc_type = DocumentType.spreadsheet_ledger if invoice_results else DocumentType.other
        doc.doc_type_confidence = 1.0 if invoice_results else 0.7
        summary = ExtractionSummary(doc.doc_type, skipped_reason=None if invoice_results else "no_invoice_columns")
    else:
        text_pages = _load_text_pages(session, document_id)
        doc_type, conf = classify_text("\n".join(p.text for p in text_pages))
        doc.doc_type, doc.doc_type_confidence = doc_type, conf
        summary = ExtractionSummary(doc_type)
        if doc_type == DocumentType.other:
            summary.skipped_reason = "not_recognised_as_invoice"
        else:
            provider = provider or get_provider(s)
            fallback = fallback if fallback is not None else get_provider(s, fallback=True)
            result = extract_invoices(text_pages, provider, fallback=fallback, settings=s)
            invoice_results = result.invoices
            summary.errors, summary.used_fallback = result.errors, result.used_fallback

    _replace_unreviewed(session, document_id, force)
    kind = "spreadsheet" if tabular else "document"
    for r in invoice_results:
        inv = _build_invoice(doc, r, kind, result.model_name if result else None,
                             result.prompt_version if result else None)
        session.add(inv)
        session.flush()
        summary.invoice_ids.append(inv.id)
    orch.log_event(session, doc.id, "extraction_complete", doc_type=doc.doc_type.value,
                   invoices=len(summary.invoice_ids), errors=summary.errors, skipped=summary.skipped_reason,
                   fallback=summary.used_fallback)
    session.flush()
    return summary
