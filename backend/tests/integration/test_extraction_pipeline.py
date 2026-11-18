import json
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.extraction.llm import LLMPermanentError, LLMProvider, LLMResponse, LLMTransientError
from app.models import Document, DocumentEmbedding, Invoice, ManualCorrection, ProcessingJob, ValidationResult
from app.models.base import DocumentStatus, DocumentType, JobStage, JobStatus, ReviewStatus
from app.services import orchestration as orch
from app.services import pipeline
from app.services.extraction_service import ExtractionConflict, run_extraction
from tests.integration.test_pipeline import drain, upload
from tests.integration.test_schema import make_user
from tests.unit import factories as f

TEXT = """ACME Traders Ltd
Invoice No: INV-2025-0042
Date: 15/01/2025
Bill To: Rahim Enterprise
1 Cement bag 10 100.00 1,000.00
Subtotal 1,000.00
VAT 150.00
Total BDT 1,150.00"""
LINE = "1 Cement bag 10 100.00 1,000.00"


def fld(value, evidence):
    return {"value": value, "evidence": evidence, "page": 1}


NULL = {"value": None, "evidence": None, "page": None}


def inv_json(**over):
    inv = {
        "invoice_number": fld("INV-2025-0042", "Invoice No: INV-2025-0042"),
        "invoice_date": fld("15/01/2025", "Date: 15/01/2025"),
        "supplier_name": fld("ACME Traders Ltd", "ACME Traders Ltd"),
        "customer_name": fld("Rahim Enterprise", "Bill To: Rahim Enterprise"),
        "subtotal": fld("1,000.00", "Subtotal 1,000.00"), "tax": fld("150.00", "VAT 150.00"),
        "total": fld("1,150.00", "Total BDT 1,150.00"), "currency": fld("BDT", "Total BDT 1,150.00"),
        "line_items": [{"description": fld("Cement bag", LINE), "quantity": fld("10", LINE),
                        "unit_price": fld("100.00", LINE), "tax": NULL, "total": fld("1,000.00", LINE)}],
    }
    inv.update(over)
    return inv


class Const(LLMProvider):
    name = "const"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, messages):
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return LLMResponse(r if isinstance(r, str) else json.dumps({"invoices": r}), "test-model-1")


@pytest.fixture
def storage(tmp_path):
    from app.services.storage import LocalStorage
    return LocalStorage(tmp_path / "store")


@pytest.fixture
def user(db):
    return make_user(db, f"{uuid.uuid4().hex[:8]}@example.com")


def invoice_pdf(tmp_path, text=TEXT, name="inv.pdf"):
    return f.digital_pdf(tmp_path / name, [text])


def invoices(db, doc):
    return db.scalars(select(Invoice).where(Invoice.document_id == doc.id).order_by(Invoice.created_at)).all()


def findings(db, inv):
    return {r.rule_id: r for r in db.scalars(select(ValidationResult).where(ValidationResult.invoice_id == inv.id))}


def run_all(db, storage, tmp_path, user, llm, text=TEXT, name="inv.pdf", settings=None):
    res = upload(db, storage, user, invoice_pdf(tmp_path, text, name))
    outcomes = drain(db, storage, full=True, llm=llm, settings=settings)
    return res, outcomes


# ---------- full chain ----------
def test_full_chain_extracts_validates_and_marks_processed(db, storage, user, tmp_path):
    llm = Const([inv_json()])
    res, outcomes = run_all(db, storage, tmp_path, user, llm)
    assert [o.status for o in outcomes] == ["succeeded"] * 5  # plan, 1 batch, ai_extract, validate, embed
    doc = db.get(Document, res.document.id)
    assert db.scalar(select(func.count()).select_from(DocumentEmbedding)
                     .where(DocumentEmbedding.document_id == doc.id)) >= 1
    assert doc.status == DocumentStatus.processed and doc.doc_type == DocumentType.invoice and doc.error is None
    (inv,) = invoices(db, doc)
    assert (inv.invoice_number, inv.invoice_date, inv.supplier_name, inv.customer_name, inv.currency) == (
        "INV-2025-0042", date(2025, 1, 15), "ACME Traders Ltd", "Rahim Enterprise", "BDT")
    assert (inv.subtotal, inv.tax, inv.total) == (D("1000"), D("150"), D("1150"))
    assert inv.model_name == "test-model-1" and inv.prompt_version == "invoice-v1" and inv.source_kind == "document"
    assert not inv.requires_review and inv.review_status == ReviewStatus.not_required
    (li,) = inv.line_items
    assert (li.description, li.quantity, li.unit_price, li.total) == ("Cement bag", D("10"), D("100"), D("1000"))
    assert [r for r in findings(db, inv).values() if r.severity.value != "info"] == []


def test_traceability_confidence_references_and_original_text(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, Const([inv_json()]))
    (inv,) = invoices(db, res.document)
    assert inv.confidence["total"]["score"] == 1.0 and inv.confidence["total"]["status"] == "verified"
    assert inv.confidence["customer_name"]["status"] == "verified"
    ref = next(r for r in inv.source_references if r["field"] == "total")
    assert ref["page"] == 1 and "1,150.00" in ref["text"]
    assert inv.original_text["total"] == "1,150.00"  # original extracted text preserved
    assert inv.raw_extraction["total"]["value"] == "1,150.00"
    assert inv.line_items[0].source_references[0]["field"].startswith("line_items[1].")
    assert inv.original_text["_flags"]["detected_currencies"] == ["BDT"]


def test_hallucinated_total_is_dropped_flagged_and_sent_to_review(db, storage, user, tmp_path):
    bad = inv_json(total=fld("1,200.00", "Total BDT 1,200.00"))
    res, _ = run_all(db, storage, tmp_path, user, Const([bad]))
    (inv,) = invoices(db, res.document)
    assert inv.total is None                                   # never stored as fact
    assert inv.raw_extraction["total"]["value"] == "1,200.00"  # but the proposal is kept for the reviewer
    assert inv.confidence["total"]["status"] == "rejected"
    assert inv.requires_review and inv.review_status == ReviewStatus.pending
    assert "field_rejected:total:value_not_in_evidence" in inv.original_text["_flags"]["review_reasons"]
    assert "required_field_missing" in findings(db, inv)
    doc = db.get(Document, res.document.id)
    assert doc.status == DocumentStatus.needs_review and "invoice_needs_review" in doc.metadata_["review_reasons"]


def test_arithmetic_error_in_source_is_caught_by_deterministic_validation(db, storage, user, tmp_path):
    text = TEXT.replace("Total BDT 1,150.00", "Total BDT 1,200.00")  # the document itself is inconsistent
    bad = inv_json(total=fld("1,200.00", "Total BDT 1,200.00"))
    res, _ = run_all(db, storage, tmp_path, user, Const([bad]), text=text)
    (inv,) = invoices(db, res.document)
    assert inv.total == D("1200")  # extraction faithfully reports what the document says
    r = findings(db, inv)["total_mismatch"]
    assert (r.expected_value, r.actual_value) == ("1150.00", "1200.00")
    assert db.get(Document, res.document.id).status == DocumentStatus.needs_review


# ---------- routing ----------
def test_non_invoice_document_skips_llm_and_goes_to_review(db, storage, user, tmp_path):
    llm = Const([inv_json()])
    res, _ = run_all(db, storage, tmp_path, user, llm, text="Minutes of the weekly meeting. Agenda: hiring plan.")
    doc = db.get(Document, res.document.id)
    assert llm.calls == 0 and doc.doc_type == DocumentType.other
    assert doc.status == DocumentStatus.needs_review
    assert doc.metadata_["review_reasons"] == ["not_recognised_as_invoice"]
    assert invoices(db, doc) == []


def test_non_invoice_can_be_configured_to_complete_without_review(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, Const([]), text="Minutes of the weekly meeting. Agenda: hiring.",
                     settings=Settings(extraction_review_non_invoices=False))
    assert db.get(Document, res.document.id).status == DocumentStatus.processed


def test_default_mock_provider_never_fabricates(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, None)  # no llm injected -> settings default (mock, no fixtures)
    doc = db.get(Document, res.document.id)
    assert invoices(db, doc) == [] and doc.status == DocumentStatus.needs_review
    assert doc.metadata_["review_reasons"] == ["no_invoices_extracted"]


def test_malformed_model_output_surfaces_error_and_review(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, Const("garbage"))
    doc = db.get(Document, res.document.id)
    assert "model output invalid" in doc.error
    assert doc.status == DocumentStatus.needs_review and "extraction_errors" in doc.metadata_["review_reasons"]


def test_out_of_range_amount_is_dropped_with_reason_not_a_db_crash(db, storage, user, tmp_path):
    huge = "999,999,999,999,999.00"
    text = TEXT.replace("1,150.00", huge)
    bad = inv_json(total=fld(huge, f"Total BDT {huge}"))
    res, _ = run_all(db, storage, tmp_path, user, Const([bad]), text=text)
    (inv,) = invoices(db, res.document)
    assert inv.total is None and "value_out_of_range:total" in inv.original_text["_flags"]["review_reasons"]


# ---------- spreadsheets ----------
def test_spreadsheet_rows_become_traceable_invoices_without_llm(db, storage, user, tmp_path):
    llm = Const([inv_json()])
    res = upload(db, storage, user, f.xlsx(tmp_path / "book.xlsx"))
    drain(db, storage, full=True, llm=llm)
    doc = db.get(Document, res.document.id)
    inv1, inv2 = sorted(invoices(db, doc), key=lambda i: i.invoice_number)
    assert llm.calls == 0 and doc.doc_type == DocumentType.spreadsheet_ledger
    assert (inv1.invoice_number, inv1.total, inv1.supplier_name, inv1.invoice_date) == (
        "INV-001", D("1150.5"), "Rahim Traders", date(2025, 1, 5))
    assert inv2.total == D("0") and inv2.confidence["total"]["status"] == "verified"  # zero is a value
    assert inv1.source_kind == "spreadsheet" and inv1.row_ref == "Invoices!4"
    ref = next(r for r in inv1.source_references if r["field"] == "total")
    assert ref["text"] == "Invoices!D4"                                       # exact cell
    assert any(s["sheet"] == "Lines" for s in doc.metadata_["tabular_skipped"])  # unrecognised sheet reported
    assert "currency_missing" in findings(db, inv1)


def test_csv_rows_with_same_invoice_number_become_one_invoice_with_lines(db, storage, user, tmp_path):
    csv = tmp_path / "lines.csv"
    csv.write_text("Invoice No,Date,Supplier,Description,Qty,Unit Price,Line Total,Total,Currency\n"
                   "INV-7,2025-03-02,ACME,Cement,10,100,1000,1500,BDT\n"
                   "INV-7,2025-03-02,ACME,Sand,5,100,500,1500,BDT\n"
                   "INV-8,2025-03-03,ACME,Gravel,1,200,200,200,BDT\n")
    res = upload(db, storage, user, csv)
    drain(db, storage, full=True)
    a, b = sorted(invoices(db, res.document), key=lambda i: i.invoice_number)
    assert [(li.description, li.quantity, li.total) for li in a.line_items] == [
        ("Cement", D("10"), D("1000")), ("Sand", D("5"), D("500"))]
    assert a.total == D("1500") and len(b.line_items) == 1
    assert "line_total_mismatch" not in findings(db, a)


def test_unparseable_cell_is_flagged_not_guessed(db, storage, user, tmp_path):
    csv = tmp_path / "bad.csv"
    csv.write_text("Invoice No,Date,Supplier,Total\nINV-1,2025-03-02,ACME,N/A\n")
    res = upload(db, storage, user, csv)
    drain(db, storage, full=True)
    (inv,) = invoices(db, res.document)
    assert inv.total is None and inv.original_text["total"] == "N/A"
    assert "field_unparseable:total" in inv.original_text["_flags"]["review_reasons"]


# ---------- re-extraction safety ----------
def test_rerun_replaces_unreviewed_invoices_without_duplicating(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, Const([inv_json()]))
    first = invoices(db, res.document)[0].id
    run_extraction(db, res.document.id, provider=Const([inv_json()]))
    after = invoices(db, res.document)
    assert len(after) == 1 and after[0].id != first


def test_rerun_refuses_to_overwrite_reviewed_work_unless_forced(db, storage, user, tmp_path):
    res, _ = run_all(db, storage, tmp_path, user, Const([inv_json()]))
    (inv,) = invoices(db, res.document)
    db.add(ManualCorrection(invoice_id=inv.id, field_name="supplier_name", old_value="ACME", new_value="ACME Ltd",
                            user_id=user.id))
    db.flush()
    with pytest.raises(ExtractionConflict):
        run_extraction(db, res.document.id, provider=Const([inv_json()]))
    assert [i.id for i in invoices(db, res.document)] == [inv.id]  # untouched
    run_extraction(db, res.document.id, provider=Const([inv_json()]), force=True)
    assert invoices(db, res.document)[0].id != inv.id


# ---------- job-level error handling ----------
def test_transient_llm_error_retries_with_backoff_then_succeeds(db, storage, user, tmp_path):
    llm = Const(LLMTransientError("429"), [inv_json()])
    res = upload(db, storage, user, invoice_pdf(tmp_path))
    drain(db, storage)  # text stage only; ai_extract job is now published but not run
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == res.document.id,
                                                ProcessingJob.stage == JobStage.ai_extract))
    first = pipeline.run_job(db, storage, job.id, llm=llm)
    assert first.status == "retry" and first.countdown == 5
    db.refresh(job)
    assert job.status == JobStatus.pending and "429" in job.error
    assert pipeline.run_job(db, storage, job.id, llm=llm).status == "succeeded"
    assert db.get(Document, res.document.id).status == DocumentStatus.validating_financials


def test_permanent_llm_error_fails_immediately_and_is_recoverable(db, storage, user, tmp_path):
    res = upload(db, storage, user, invoice_pdf(tmp_path))
    drain(db, storage)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == res.document.id,
                                                ProcessingJob.stage == JobStage.ai_extract))
    out = pipeline.run_job(db, storage, job.id, llm=Const(LLMPermanentError("401 bad key")))
    assert out.status == "failed"
    doc = db.get(Document, res.document.id)
    assert doc.status == DocumentStatus.failed and "401" in doc.error
    orch.retry_job(db, job)  # operator fixes the key, then retries
    assert orch.queue_of(db, job) == "default"
    assert pipeline.run_job(db, storage, job.id, llm=Const([inv_json()])).status == "succeeded"


# ---------- cross-document ----------
def test_duplicate_invoice_in_second_document_is_flagged_not_dropped(db, storage, user, tmp_path):
    first, _ = run_all(db, storage, tmp_path, user, Const([inv_json()]), name="a.pdf")
    second, _ = run_all(db, storage, tmp_path, user, Const([inv_json()]), text=TEXT + "\nPage copy", name="b.pdf")
    (a,), (b,) = invoices(db, first.document), invoices(db, second.document)
    assert "duplicate_invoice" in findings(db, b) and a.id != b.id
    assert db.get(Document, second.document.id).status == DocumentStatus.needs_review
    assert db.get(Invoice, a.id) is not None  # nothing deleted
