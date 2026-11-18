"""Job lifecycle: creation, outbox, relay, retry, cancel, progress.

All functions take a Session and flush; callers own the commit so a job row and its outbox
row always land in the same transaction.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Document, DocumentPage, Outbox, ProcessingHistory, ProcessingJob
from app.models.base import DocumentStatus, JobStage, JobStatus

TERMINAL = (JobStatus.succeeded, JobStatus.failed, JobStatus.dead, JobStatus.cancelled)
Publisher = Callable[[dict[str, Any]], None]


def idempotency_key(document_id: uuid.UUID, stage: JobStage, start: int | None, end: int | None, version: str) -> str:
    raw = f"{document_id}:{stage.value}:{start}:{end}:{version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def log_event(session: Session, document_id: uuid.UUID, event: str, job_id: uuid.UUID | None = None,
              actor: str = "system", **detail: Any) -> None:
    session.add(ProcessingHistory(document_id=document_id, job_id=job_id, event=event, actor=actor, detail=detail))


def queue_for(filename_ext: str) -> str:
    """OCR-heavy formats get their own queue so they cannot starve light work."""
    return "ocr" if filename_ext.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"} else "default"


def create_job(
    session: Session, document: Document, stage: JobStage, *, parent: ProcessingJob | None = None,
    page_start: int | None = None, page_end: int | None = None, queue: str = "default",
    settings: Settings | None = None,
) -> ProcessingJob | None:
    """Create a job plus its outbox event. Returns None if an identical job already exists (idempotent)."""
    s = settings or get_settings()
    key = idempotency_key(document.id, stage, page_start, page_end, s.pipeline_version)
    if parent is not None:
        key = hashlib.sha256(f"{parent.id}:{key}".encode()).hexdigest()
    if session.scalar(select(ProcessingJob.id).where(ProcessingJob.idempotency_key == key)):
        return None
    job = ProcessingJob(
        document_id=document.id, parent_job_id=parent.id if parent else None, stage=stage,
        page_start=page_start, page_end=page_end, idempotency_key=key, max_attempts=s.job_max_attempts,
    )
    session.add(job)
    session.flush()
    enqueue(session, job, queue)
    return job


def enqueue(session: Session, job: ProcessingJob, queue: str) -> None:
    session.add(Outbox(job_id=job.id, payload={"job_id": str(job.id), "queue": queue}))
    session.flush()


def relay_outbox(session: Session, publisher: Publisher, limit: int = 100) -> int:
    """Publish unpublished outbox rows. Safe to run from several relays (SKIP LOCKED).

    A row is marked published only after the publisher returns; a crash in between causes a
    re-publish, which is harmless because run_job is idempotent.
    """
    rows = session.scalars(
        select(Outbox).where(Outbox.published_at.is_(None)).order_by(Outbox.created_at)
        .limit(limit).with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        publisher(row.payload)
        row.published_at = datetime.now(UTC)
    session.commit()
    return len(rows)


def backoff_seconds(attempt: int, settings: Settings | None = None) -> int:
    s = settings or get_settings()
    return min(s.retry_backoff_base_s * (2 ** max(attempt - 1, 0)), s.retry_backoff_max_s)


def children(session: Session, parent_id: uuid.UUID) -> list[ProcessingJob]:
    return list(session.scalars(select(ProcessingJob).where(ProcessingJob.parent_job_id == parent_id)))


def queue_of(session: Session, job: ProcessingJob) -> str:
    if job.stage in (JobStage.ai_extract, JobStage.validate_financials, JobStage.embed):
        return "default"  # network-bound, must not occupy the CPU-bound OCR workers
    doc = session.get(Document, job.document_id)
    return queue_for(Path(doc.storage_key).suffix) if doc else "default"


def retry_job(session: Session, job: ProcessingJob, actor: str = "user") -> ProcessingJob:
    """Manual retry of a failed/dead job. Resets attempts, re-opens the parent, re-enqueues."""
    if job.status not in (JobStatus.failed, JobStatus.dead):
        raise ValueError(f"Only failed or dead jobs can be retried (job is {job.status.value})")
    job.status, job.attempts, job.error, job.cancel_requested = JobStatus.pending, 0, None, False
    job.finished_at = None
    if job.parent_job_id:
        parent = session.get(ProcessingJob, job.parent_job_id)
        if parent and parent.status in TERMINAL:
            parent.status, parent.finished_at, parent.error = JobStatus.running, None, None
    doc = session.get(Document, job.document_id)
    if doc:
        doc.status, doc.error = DocumentStatus.extracting_text, None
    log_event(session, job.document_id, "job_retry_requested", job.id, actor=actor)
    enqueue(session, job, queue_of(session, job))
    return job


def cancel_job(session: Session, job: ProcessingJob, actor: str = "user") -> int:
    """Cancel a job and its non-terminal children. Running jobs stop at the next page boundary."""
    targets = [job, *children(session, job.id)]
    changed = 0
    for j in targets:
        if j.status in TERMINAL:
            continue
        j.cancel_requested = True
        if j.status == JobStatus.pending:
            j.status, j.finished_at = JobStatus.cancelled, datetime.now(UTC)
        changed += 1
    log_event(session, job.document_id, "cancel_requested", job.id, actor=actor, jobs=changed)
    session.flush()
    # Jobs that were still pending are cancelled immediately and will never run, so nothing else
    # would ever update the document; close it here. Running jobs finish via the worker.
    root = job if job.parent_job_id is None else session.get(ProcessingJob, job.parent_job_id)
    if root.status == JobStatus.cancelled:
        session.get(Document, root.document_id).status = DocumentStatus.cancelled
    else:
        finalize_if_complete(session, root)
    session.flush()
    return changed


def progress(session: Session, document_id: uuid.UUID) -> dict[str, Any]:
    """Document- and page-level progress computed from stored rows (no cached counters to drift)."""
    doc = session.get(Document, document_id)
    page_counts = dict(session.execute(
        select(DocumentPage.status, func.count()).where(DocumentPage.document_id == document_id)
        .group_by(DocumentPage.status)
    ).all())
    job_counts = dict(session.execute(
        select(ProcessingJob.status, func.count()).where(ProcessingJob.document_id == document_id)
        .group_by(ProcessingJob.status)
    ).all())
    done = page_counts.get("ok", 0) + page_counts.get("empty", 0)
    total = doc.page_count if doc else None
    return {
        "status": doc.status.value if doc else None,
        "pages_total": total,
        "pages_done": done,
        "pages_failed": page_counts.get("failed", 0),
        "percent": round(100 * done / total, 1) if total else None,
        "jobs": {k.value: v for k, v in job_counts.items()},
    }


def finalize_if_complete(session: Session, root: ProcessingJob) -> None:
    """Close the root job once every child is terminal. Locks the root row to avoid double-finalising."""
    session.execute(select(ProcessingJob.id).where(ProcessingJob.id == root.id).with_for_update())
    session.refresh(root)
    if root.status in TERMINAL:
        return
    kids = children(session, root.id)
    if not kids or any(k.status not in TERMINAL for k in kids):
        return
    doc = session.get(Document, root.document_id)
    now = datetime.now(UTC)
    bad = [k for k in kids if k.status != JobStatus.succeeded]
    if not bad:
        root.status, root.finished_at = JobStatus.succeeded, now
        # Text extraction is only the first stage: the document is NOT "processed" until AI
        # extraction and validation have run, so it moves on to the next stage's status.
        doc.status, doc.error = DocumentStatus.ai_extracting, None
        log_event(session, root.document_id, "text_extraction_complete", root.id, pages=doc.page_count)
        create_job(session, doc, JobStage.ai_extract, queue="default")  # next stage; idempotent by key
        return
    cancelled = all(k.status == JobStatus.cancelled for k in bad)
    root.status, root.finished_at = (JobStatus.cancelled if cancelled else JobStatus.failed), now
    detail = "; ".join(f"pages {k.page_start}-{k.page_end}: {k.error or k.status.value}" for k in bad)[:2000]
    root.error = detail
    doc.status = DocumentStatus.cancelled if cancelled else DocumentStatus.failed
    doc.error = None if cancelled else detail
    log_event(session, root.document_id, "text_extraction_incomplete", root.id, failed=len(bad))


def reset_stuck_running(session: Session, older_than_s: int) -> int:
    """Re-queue jobs left 'running' by a crashed worker (called by a periodic janitor task)."""
    cutoff = datetime.now(UTC).timestamp() - older_than_s
    stuck = session.scalars(select(ProcessingJob).where(
        ProcessingJob.status == JobStatus.running,
        func.extract("epoch", ProcessingJob.started_at) < cutoff,
    )).all()
    for j in stuck:
        session.execute(update(ProcessingJob).where(ProcessingJob.id == j.id).values(status=JobStatus.pending))
        enqueue(session, j, queue_of(session, j))
    session.flush()
    return len(stuck)
