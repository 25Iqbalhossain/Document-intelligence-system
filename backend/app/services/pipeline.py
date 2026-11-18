"""Job execution: text-extraction stage (plan -> page-batch jobs -> persisted pages).

Framework-free (no Celery imports) so it is directly testable. Every write is an upsert keyed on
natural keys, so re-running a job or a whole batch is idempotent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.extraction.llm import LLMPermanentError, LLMProvider
from app.models import Document, DocumentPage, ExtractedText, Invoice, ProcessingJob
from app.models.base import DocumentStatus, JobStage, JobStatus
from app.processors.base import PageResult, PageStatus, ProcessingError, ValidationError
from app.processors.ocr import OcrEngine, TesseractEngine
from app.processors.registry import get_processor
from app.search.embeddings import EmbeddingProvider, get_embedder
from app.services import orchestration as orch
from app.services.embedding_service import run_embedding
from app.services.extraction_service import ExtractionConflict, run_extraction
from app.services.storage import StorageBackend
from app.services.validation_service import run_validation

log = logging.getLogger("docint.pipeline")


class JobCancelled(Exception):
    pass


class PagesFailed(Exception):
    """Some pages could not be processed. Treated as transient: re-running only redoes the batch."""

    def __init__(self, pages: list[tuple[int, str]]) -> None:
        super().__init__(f"{len(pages)} page(s) failed")
        self.pages = pages


@dataclass(slots=True)
class Outcome:
    status: str  # succeeded | retry | failed | dead | cancelled | skipped | missing
    countdown: int | None = None


def _clean(text: str) -> str:
    return text.replace("\x00", "")  # PostgreSQL text cannot contain NUL


def run_job(
    session: Session, storage: StorageBackend, job_id, *, settings: Settings | None = None,
    ocr: OcrEngine | None = None, llm: LLMProvider | None = None, llm_fallback: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
) -> Outcome:
    s = settings or get_settings()
    job = session.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
    if job is None:
        return Outcome("missing")
    if job.status in orch.TERMINAL:
        session.rollback()
        return Outcome("skipped")  # idempotent: duplicate delivery of a finished job is a no-op
    if job.cancel_requested:
        return _finish_cancelled(session, job)

    job.status, job.started_at = JobStatus.running, datetime.now(UTC)
    job.attempts += 1
    orch.log_event(session, job.document_id, "job_started", job.id, attempt=job.attempts, stage=job.stage.value)
    session.commit()

    engine = ocr or TesseractEngine(languages=s.ocr_languages)
    try:
        if job.stage in (JobStage.ai_extract, JobStage.validate_financials, JobStage.embed):
            _run_single_stage(session, job, s, llm, llm_fallback, embedder)
            job.status, job.finished_at, job.error = JobStatus.succeeded, datetime.now(UTC), None
            orch.log_event(session, job.document_id, "job_succeeded", job.id, stage=job.stage.value)
            session.commit()
            return Outcome("succeeded")
        if job.parent_job_id is None:
            _plan(session, storage, job, s, engine)
            session.commit()
            return Outcome("succeeded")
        _run_batch(session, storage, job, s, engine)
        job.status, job.finished_at, job.error = JobStatus.succeeded, datetime.now(UTC), None
        orch.log_event(session, job.document_id, "job_succeeded", job.id, pages=f"{job.page_start}-{job.page_end}")
        orch.finalize_if_complete(session, session.get(ProcessingJob, job.parent_job_id))
        session.commit()
        return Outcome("succeeded")
    except JobCancelled:
        session.rollback()
        job = session.get(ProcessingJob, job_id)
        return _finish_cancelled(session, job)
    except Exception as exc:
        session.rollback()
        return _handle_failure(session, job_id, exc, s)


def _finish_cancelled(session: Session, job: ProcessingJob) -> Outcome:
    job.status, job.finished_at = JobStatus.cancelled, datetime.now(UTC)
    orch.log_event(session, job.document_id, "job_cancelled", job.id)
    if job.parent_job_id:
        orch.finalize_if_complete(session, session.get(ProcessingJob, job.parent_job_id))
    else:
        doc = session.get(Document, job.document_id)
        doc.status = DocumentStatus.cancelled
    session.commit()
    return Outcome("cancelled")


def _handle_failure(session: Session, job_id, exc: Exception, s: Settings) -> Outcome:
    job = session.get(ProcessingJob, job_id)
    permanent = isinstance(exc, (ValidationError, LLMPermanentError, ExtractionConflict)) or (
        isinstance(exc, ProcessingError) and exc.code in {"corrupt_file", "bad_page_range", "encoding_undetected"}
    )
    message = _describe(exc)
    job.error = message
    log.warning("job_failed", extra={"job_id": str(job.id), "attempt": job.attempts, "error": message})
    if permanent:
        job.status, job.finished_at = JobStatus.failed, datetime.now(UTC)
        outcome = Outcome("failed")
    elif job.attempts >= job.max_attempts:
        job.status, job.finished_at = JobStatus.dead, datetime.now(UTC)  # dead-letter: visible, manually retryable
        outcome = Outcome("dead")
    else:
        job.status = JobStatus.pending
        outcome = Outcome("retry", countdown=orch.backoff_seconds(job.attempts, s))
    orch.log_event(session, job.document_id, "job_failed", job.id, outcome=outcome.status, error=message,
                   attempt=job.attempts)
    if outcome.status != "retry":
        if job.stage == JobStage.embed:
            # Search indexing is not worth losing reviewed data over: send the document to a human with the reason.
            finalize_document(session, session.get(Document, job.document_id), job.id, ("search_indexing_failed",))
        elif job.parent_job_id:
            orch.finalize_if_complete(session, session.get(ProcessingJob, job.parent_job_id))
        else:
            doc = session.get(Document, job.document_id)
            doc.status, doc.error = DocumentStatus.failed, message
    session.commit()
    return outcome


def _run_single_stage(session: Session, job: ProcessingJob, s: Settings, llm: LLMProvider | None,
                      llm_fallback: LLMProvider | None, embedder: EmbeddingProvider | None = None) -> None:
    doc = session.get(Document, job.document_id)
    if job.stage == JobStage.ai_extract:
        doc.status = DocumentStatus.ai_extracting
        summary = run_extraction(session, doc.id, provider=llm, fallback=llm_fallback, settings=s)
        # Window failures are surfaced on the document (and force review); they do not abort the stage because
        # other windows may have produced valid invoices.
        doc.error = "; ".join(summary.errors)[:2000] or None
        doc.status = DocumentStatus.validating_financials
        orch.create_job(session, doc, JobStage.validate_financials, queue="default", settings=s)
        return
    if job.stage == JobStage.embed:
        summary = run_embedding(session, doc.id, embedder or get_embedder(s))
        orch.log_event(session, doc.id, "embedding_complete", job.id, chunks=summary.chunks, model=summary.model)
        finalize_document(session, doc, job.id)
        return
    invoices = list(session.scalars(select(Invoice).where(Invoice.document_id == doc.id,
                                                           Invoice.deleted_at.is_(None))))
    for inv in invoices:
        run_validation(session, inv.id)
    reasons: list[str] = []
    if doc.error:
        reasons.append("extraction_errors")
    if any(i.requires_review for i in invoices):
        reasons.append("invoice_needs_review")
    if not invoices and (doc.doc_type.value != "other" or s.extraction_review_non_invoices):
        reasons.append("no_invoices_extracted" if doc.doc_type.value != "other" else "not_recognised_as_invoice")
    doc.metadata_ = {**(doc.metadata_ or {}), "review_reasons": reasons}
    orch.log_event(session, doc.id, "validation_complete", job.id, invoices=len(invoices), reasons=reasons)
    # The document stays in validating_financials until the search index is built; it only becomes
    # processed / needs_review in finalize_document, so a half-indexed document is never shown as done.
    orch.create_job(session, doc, JobStage.embed, queue="default", settings=s)


def finalize_document(session: Session, doc: Document, job_id, extra_reasons: tuple[str, ...] = ()) -> None:
    reasons = [*(doc.metadata_ or {}).get("review_reasons", []), *extra_reasons]
    doc.metadata_ = {**(doc.metadata_ or {}), "review_reasons": reasons}
    doc.status = DocumentStatus.needs_review if reasons else DocumentStatus.processed
    orch.log_event(session, doc.id, "pipeline_complete", job_id, status=doc.status.value, reasons=reasons)


def _describe(exc: Exception) -> str:
    if isinstance(exc, PagesFailed):
        return "; ".join(f"page {n}: {err}" for n, err in exc.pages[:20])[:2000]
    if isinstance(exc, ProcessingError):
        return f"{exc.code}: {exc.message}"[:2000]
    return f"{type(exc).__name__}: {exc}"[:2000]


def _plan(session: Session, storage: StorageBackend, root: ProcessingJob, s: Settings, ocr: OcrEngine) -> None:
    doc = session.get(Document, root.document_id)
    path = storage.path(doc.storage_key)
    processor = get_processor(path, s.max_upload_bytes, ocr)
    info = processor.inspect(path)
    doc.page_count, doc.status = info.page_count, DocumentStatus.extracting_text
    doc.metadata_ = {**(doc.metadata_ or {}), **info.metadata}
    queue = orch.queue_for(Path(doc.storage_key).suffix)
    for start, end in plan_batches(Path(doc.storage_key).suffix, info.page_count or 1, s.pdf_batch_pages):
        orch.create_job(session, doc, JobStage.extract_text, parent=root, page_start=start, page_end=end,
                        queue=queue, settings=s)
    orch.log_event(session, doc.id, "planned", root.id, pages=info.page_count)


def plan_batches(extension: str, page_count: int, batch: int) -> list[tuple[int, int]]:
    """PDFs are split into page batches; other formats are processed whole (a workbook is opened once)."""
    if extension.lower() != ".pdf":
        return [(1, max(page_count, 1))]
    return [(a, min(a + batch - 1, page_count)) for a in range(1, page_count + 1, batch)]


def _run_batch(session: Session, storage: StorageBackend, job: ProcessingJob, s: Settings, ocr: OcrEngine) -> None:
    doc = session.get(Document, job.document_id)
    path = storage.path(doc.storage_key)
    processor = get_processor(path, s.max_upload_bytes, ocr)
    failed: list[tuple[int, str]] = []
    for result in processor.process(path, job.page_start or 1, job.page_end):
        session.refresh(job, ["cancel_requested"])
        if job.cancel_requested:
            raise JobCancelled
        persist_page(session, doc.id, result)
        session.commit()  # page-level durability: progress is visible and survives a crash
        if result.status == PageStatus.failed:
            failed.append((result.page_number, result.error or "unknown error"))
    if failed:
        raise PagesFailed(failed)


def persist_page(session: Session, document_id, r: PageResult) -> None:
    text = _clean(r.text)
    meta = {**r.metadata, **({"tables": r.tables} if r.tables else {})}
    page_vals = {
        "kind": r.kind.value, "label": r.label, "ocr_confidence": r.ocr_confidence, "text_chars": len(text),
        "status": r.status.value, "error": _clean(r.error) if r.error else None, "metadata": meta,
    }
    stmt = insert(DocumentPage).values(document_id=document_id, page_number=r.page_number, **page_vals)
    session.execute(stmt.on_conflict_do_update(
        constraint="uq_document_pages_doc_page", set_={**page_vals, "updated_at": datetime.now(UTC)}))
    if text.strip():
        vals = {"text": text, "ocr_confidence": r.ocr_confidence}
        t = insert(ExtractedText).values(document_id=document_id, page_number=r.page_number, source=r.source, **vals)
        session.execute(t.on_conflict_do_update(
            constraint="uq_extracted_text_doc_page_source", set_={**vals, "updated_at": datetime.now(UTC)}))
