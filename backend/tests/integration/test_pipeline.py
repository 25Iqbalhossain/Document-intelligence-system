import io
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.models import Document, DocumentPage, ExtractedText, Outbox, ProcessingHistory, ProcessingJob
from app.models.base import DocumentStatus, JobStatus
from app.processors.base import ProcessingError, ValidationError
from app.processors.ocr import OcrResult
from app.services import orchestration as orch
from app.services import pipeline
from app.services.documents import ingest_upload, safe_display_name
from app.services.storage import LocalStorage
from tests.integration.test_schema import make_user
from tests.unit import factories as f


class FakeOcr:
    def __init__(self, text="Invoice INV-1 Total 100.00", conf=90.0):
        self.calls, self.text, self.conf = 0, text, conf

    def recognize(self, image):
        self.calls += 1
        return OcrResult(self.text, self.conf, len(self.text.split()))


class FlakyOcr(FakeOcr):
    def __init__(self, fail_times):
        super().__init__()
        self.fail_times = fail_times

    def recognize(self, image):
        if self.calls < self.fail_times:
            self.calls += 1
            raise ProcessingError("ocr_timeout", "simulated timeout")
        return super().recognize(image)


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "store")


@pytest.fixture
def user(db):
    return make_user(db, f"{uuid.uuid4().hex[:8]}@example.com")


def upload(db, storage, user, path: Path, name=None, settings=None):
    with path.open("rb") as fh:
        return ingest_upload(db, storage, owner_id=user.id, filename=name or path.name, stream=fh, settings=settings)


def drain(db, storage, ocr=None, settings=None, rounds=10, full=False, llm=None):
    """Relay the outbox and run published jobs inline. By default only the text-extraction stage runs
    (these tests are about that stage); full=True also runs ai_extract and validate_financials."""
    outcomes = []
    for _ in range(rounds):
        published = []
        if not orch.relay_outbox(db, published.append):
            break
        for p in published:
            job = db.get(ProcessingJob, uuid.UUID(p["job_id"]))
            if not full and job.stage.value != "extract_text":
                continue
            outcomes.append(pipeline.run_job(db, storage, job.id, settings=settings, ocr=ocr, llm=llm))
    return outcomes


def page_rows(db, doc):
    return db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id)
                      .order_by(DocumentPage.page_number)).all()


# ---------- happy path ----------

def test_large_pdf_split_into_batches_and_fully_persisted(db, storage, user, tmp_path):
    pdf = f.digital_pdf(tmp_path / "big.pdf", [f"Page {i} text with enough characters here" for i in range(1, 46)])
    res = upload(db, storage, user, pdf)
    outcomes = drain(db, storage)
    assert [o.status for o in outcomes] == ["succeeded"] * 4  # 1 plan + 3 batches (20+20+5)
    kids = orch.children(db, res.root_job_id)
    assert sorted((k.page_start, k.page_end) for k in kids) == [(1, 20), (21, 40), (41, 45)]
    pages = page_rows(db, res.document)
    assert [p.page_number for p in pages] == list(range(1, 46))
    assert all(p.status == "ok" and p.kind.value == "digital" for p in pages)
    db.refresh(res.document)
    assert res.document.status == DocumentStatus.ai_extracting  # text extracted but NOT 'processed'
    assert db.get(ProcessingJob, res.root_job_id).status == JobStatus.succeeded
    pr = orch.progress(db, res.document.id)
    assert pr["pages_done"] == 45 and pr["percent"] == 100.0 and pr["pages_failed"] == 0


def test_text_is_searchable_after_processing(db, storage, user, tmp_path):
    pdf = f.digital_pdf(tmp_path / "s.pdf", ["Supplier Rahim Traders invoice 4411", "Other page about cement"])
    res = upload(db, storage, user, pdf)
    drain(db, storage)
    rows = db.execute(text(
        "select page_number from extracted_text where document_id=:d and tsv @@ websearch_to_tsquery('simple','rahim')"
    ), {"d": res.document.id}).all()
    assert rows == [(1,)]


def test_spreadsheet_and_csv_flow(db, storage, user, tmp_path):
    x = upload(db, storage, user, f.xlsx(tmp_path / "a.xlsx"))
    csv = tmp_path / "a.csv"
    csv.write_text("id,total\n1,100\n2,0\n")
    c = upload(db, storage, user, csv)
    drain(db, storage)
    sheets = page_rows(db, x.document)
    assert [p.label for p in sheets] == ["Invoices", "Lines", "Empty"]
    assert sheets[0].metadata_["tables"][0]["headers"][0] == "Invoice No"
    assert sheets[2].status == "empty"
    assert db.get(Document, c.document.id).status == DocumentStatus.ai_extracting
    assert page_rows(db, c.document)[0].metadata_["tables"][0]["rows"][1] == ["2", "0"]


def test_scanned_pdf_records_ocr_confidence_separately(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.scanned_pdf(tmp_path / "s.pdf", ["Invoice INV-1"]))
    drain(db, storage, ocr=FakeOcr(conf=87.5))
    (p,) = page_rows(db, res.document)
    assert p.kind.value == "scanned" and p.ocr_confidence == 87.5
    et = db.scalars(select(ExtractedText).where(ExtractedText.document_id == res.document.id)).one()
    assert et.source == "ocr" and et.ocr_confidence == 87.5


# ---------- idempotency ----------

def test_rerun_of_finished_job_is_noop(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"] * 3))
    outcomes = drain(db, storage)
    assert all(o.status == "succeeded" for o in outcomes)
    again = pipeline.run_job(db, storage, res.root_job_id)
    assert again.status == "skipped"


def test_redelivered_batch_does_not_duplicate_rows(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"] * 3))
    drain(db, storage)
    kid = orch.children(db, res.root_job_id)[0]
    kid.status = JobStatus.pending  # simulate crash-before-ack redelivery after partial/complete work
    db.flush()
    pipeline.run_job(db, storage, kid.id)
    assert len(page_rows(db, res.document)) == 3
    n_text = db.scalar(
        select(func.count()).select_from(ExtractedText).where(ExtractedText.document_id == res.document.id)
    )
    assert n_text == 3


def test_identical_job_not_created_twice(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    root = db.get(ProcessingJob, res.root_job_id)
    assert orch.create_job(db, res.document, root.stage) is None


# ---------- upload rejection ----------

def test_truncated_pdf_rejected_and_not_stored(db, storage, user, tmp_path):
    good = f.digital_pdf(tmp_path / "g.pdf", ["Some text that is long enough"] * 3)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(good.read_bytes()[:150])
    before = db.scalar(select(func.count()).select_from(Document))
    with pytest.raises(ValidationError) as e:
        upload(db, storage, user, bad)
    assert e.value.code == "corrupt_file"
    assert not any(storage.root.rglob("*.pdf")) and not any(storage.root.rglob("*.part"))
    assert db.scalar(select(func.count()).select_from(Document)) == before


def test_oversize_upload_aborted_midstream(db, storage, user, tmp_path):
    pdf = f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"] * 5)
    with pytest.raises(ValidationError) as e:
        upload(db, storage, user, pdf, settings=Settings(max_upload_bytes=100))
    assert e.value.code == "too_large"
    assert not any(p.is_file() for p in storage.root.rglob("*"))


def test_disguised_executable_rejected(db, storage, user):
    with pytest.raises(ValidationError) as e:
        ingest_upload(db, storage, owner_id=user.id, filename="invoice.pdf", stream=io.BytesIO(b"MZ" + b"\0" * 500))
    assert e.value.code == "signature_mismatch"


def test_path_traversal_blocked(storage):
    with pytest.raises(ValidationError):
        storage.path("../../etc/passwd")
    with pytest.raises(ValidationError):
        storage.path("/etc/passwd")


def test_filenames_sanitised_and_never_used_on_disk(db, storage, user, tmp_path):
    assert safe_display_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert safe_display_name("C:\\x\\inv<oice>.pdf") == "invoice.pdf"
    pdf = f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"])
    res = upload(db, storage, user, pdf, name="../../evil.pdf")
    assert ".." not in res.document.storage_key and res.document.original_filename == "evil.pdf"


def test_duplicate_upload_is_flagged_not_blocked(db, storage, user, tmp_path):
    pdf = f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"])
    first = upload(db, storage, user, pdf)
    second = upload(db, storage, user, pdf)
    assert second.duplicate_of == [first.document.id] and second.document.id != first.document.id


# ---------- retry / dead letter ----------

def test_transient_failure_retries_with_exponential_backoff_then_succeeds(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.scanned_pdf(tmp_path / "s.pdf", ["Invoice INV-1"]))
    ocr = FlakyOcr(fail_times=2)
    drain(db, storage, ocr=ocr)  # plan + first batch attempt (fails -> retry)
    kid = orch.children(db, res.root_job_id)[0]
    assert kid.status == JobStatus.pending and kid.attempts == 1 and "ocr_timeout" in kid.error
    o2 = pipeline.run_job(db, storage, kid.id, ocr=ocr)
    o3 = pipeline.run_job(db, storage, kid.id, ocr=ocr)
    assert o2.status == "retry" and o3.status == "succeeded"
    db.refresh(kid)
    assert kid.attempts == 3 and kid.status == JobStatus.succeeded and kid.error is None
    assert db.get(Document, res.document.id).status == DocumentStatus.ai_extracting


def test_backoff_values():
    s = Settings(retry_backoff_base_s=5, retry_backoff_max_s=300)
    assert [orch.backoff_seconds(a, s) for a in (1, 2, 3, 4, 10)] == [5, 10, 20, 40, 300]


def test_exhausted_retries_go_dead_then_manual_retry_recovers(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.scanned_pdf(tmp_path / "s.pdf", ["Invoice INV-1"]))
    broken = FlakyOcr(fail_times=99)
    drain(db, storage, ocr=broken)
    kid = orch.children(db, res.root_job_id)[0]
    assert [pipeline.run_job(db, storage, kid.id, ocr=broken).status for _ in range(2)] == ["retry", "dead"]
    db.refresh(kid)
    assert kid.status == JobStatus.dead and kid.attempts == 3
    doc = db.get(Document, res.document.id)
    assert doc.status == DocumentStatus.failed and "ocr_timeout" in doc.error
    assert db.get(ProcessingJob, res.root_job_id).status == JobStatus.failed

    orch.retry_job(db, kid)  # operator retry after the cause is fixed
    assert kid.status == JobStatus.pending and kid.attempts == 0
    drain(db, storage, ocr=FakeOcr())
    db.refresh(kid)
    assert kid.status == JobStatus.succeeded
    db.refresh(doc)
    assert doc.status == DocumentStatus.ai_extracting and doc.error is None
    assert db.get(ProcessingJob, res.root_job_id).status == JobStatus.succeeded


def test_corrupt_file_fails_permanently_without_retry(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    storage.path(res.document.storage_key).write_bytes(b"%PDF-1.4 now corrupted")  # corrupted after upload
    outcomes = drain(db, storage)
    assert outcomes[0].status == "failed"
    root = db.get(ProcessingJob, res.root_job_id)
    assert root.status == JobStatus.failed and root.attempts == 1
    assert db.get(Document, res.document.id).status == DocumentStatus.failed


def test_retry_rejected_for_non_failed_job(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    with pytest.raises(ValueError):
        orch.retry_job(db, db.get(ProcessingJob, res.root_job_id))


# ---------- cancellation ----------

def test_cancel_before_start(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"] * 3))
    root = db.get(ProcessingJob, res.root_job_id)
    orch.cancel_job(db, root)
    db.commit()
    outcomes = drain(db, storage)
    assert [o.status for o in outcomes] == ["skipped"]  # already cancelled, never executed
    assert db.get(Document, res.document.id).status == DocumentStatus.cancelled
    assert page_rows(db, res.document) == []


def test_cancel_mid_batch_stops_at_page_boundary(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.scanned_pdf(tmp_path / "s.pdf", ["Invoice INV-1"], extra_blank=True))
    pipeline.run_job(db, storage, res.root_job_id, ocr=FakeOcr())  # plan only
    kid = orch.children(db, res.root_job_id)[0]

    class CancellingOcr(FakeOcr):
        def recognize(self, image):
            out = super().recognize(image)
            if self.calls == 2:  # while page 2 is being read, a user cancels
                db.execute(text("update processing_jobs set cancel_requested = true where id = :i"), {"i": kid.id})
                db.commit()
            return out

    outcome = pipeline.run_job(db, storage, kid.id, ocr=CancellingOcr())
    assert outcome.status == "cancelled"
    assert [p.page_number for p in page_rows(db, res.document)] == [1]  # page 1 kept, page 2 not written
    assert db.get(Document, res.document.id).status == DocumentStatus.cancelled


# ---------- outbox ----------

def test_outbox_relay_publishes_once(db, storage, user, tmp_path):
    upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    got = []
    assert orch.relay_outbox(db, got.append) == 1
    assert orch.relay_outbox(db, got.append) == 0
    assert len(got) == 1 and got[0]["queue"] == "ocr"


def test_outbox_row_stays_unpublished_if_broker_fails(db, storage, user, tmp_path):
    upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    db.commit()

    def broken(_):
        raise ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        orch.relay_outbox(db, broken)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None))) == 1
    got = []
    assert orch.relay_outbox(db, got.append) == 1  # delivered once the broker is back


def test_history_records_lifecycle(db, storage, user, tmp_path):
    res = upload(db, storage, user, f.digital_pdf(tmp_path / "d.pdf", ["Some text that is long enough"]))
    drain(db, storage)
    events = [e for (e,) in db.execute(select(ProcessingHistory.event).where(
        ProcessingHistory.document_id == res.document.id).order_by(ProcessingHistory.created_at))]
    assert events[0] == "uploaded" and "planned" in events and "text_extraction_complete" in events


def test_cancel_after_planning_closes_document(db, storage, user, tmp_path):
    pdf = f.digital_pdf(tmp_path / "d.pdf", [f"Page {i} has enough text to be digital" for i in range(1, 46)])
    res = upload(db, storage, user, pdf)
    pipeline.run_job(db, storage, res.root_job_id)  # plan only: 3 pending batch jobs
    root = db.get(ProcessingJob, res.root_job_id)
    orch.cancel_job(db, root)
    db.commit()
    assert {k.status for k in orch.children(db, root.id)} == {JobStatus.cancelled}
    db.refresh(root)
    assert root.status == JobStatus.cancelled
    assert db.get(Document, res.document.id).status == DocumentStatus.cancelled
