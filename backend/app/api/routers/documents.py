from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PRIVILEGED, current_user, get_document_for, get_storage, limiter, require_role
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import Document, DocumentPage, ExtractedText, Invoice, ProcessingHistory, ProcessingJob, User
from app.models.base import DocumentStatus, UserRole
from app.processors.base import ProcessingError, ValidationError
from app.services import orchestration as orch
from app.services.documents import ingest_upload
from app.services.storage import StorageBackend

router = APIRouter(tags=["documents"])
WRITERS = (UserRole.admin, UserRole.reviewer, UserRole.uploader)


class DocumentOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    doc_type: str
    status: str
    error: str | None
    created_at: datetime

    @classmethod
    def of(cls, d: Document) -> DocumentOut:
        return cls(id=d.id, original_filename=d.original_filename, mime_type=d.mime_type, size_bytes=d.size_bytes,
                   page_count=d.page_count, doc_type=d.doc_type.value, status=d.status.value, error=d.error,
                   created_at=d.created_at)


class UploadOut(BaseModel):
    document: DocumentOut
    job_id: uuid.UUID
    duplicate_of: list[uuid.UUID]


class JobOut(BaseModel):
    id: uuid.UUID
    parent_job_id: uuid.UUID | None
    stage: str
    status: str
    attempts: int
    max_attempts: int
    page_start: int | None
    page_end: int | None
    error: str | None

    @classmethod
    def of(cls, j: ProcessingJob) -> JobOut:
        return cls(id=j.id, parent_job_id=j.parent_job_id, stage=j.stage.value, status=j.status.value,
                   attempts=j.attempts, max_attempts=j.max_attempts, page_start=j.page_start, page_end=j.page_end,
                   error=j.error)


def _publish(db: Session) -> None:
    """Hand outbox rows to Celery right away; if the broker is down the beat relay delivers them later."""
    try:
        from app.workers.tasks import celery_publisher

        orch.relay_outbox(db, celery_publisher)
    except Exception:  # noqa: BLE001 - the outbox row stays unpublished and is retried by the periodic relay
        db.rollback()


@router.post("/documents", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
def upload(
    file: UploadFile, request: Request, db: Session = Depends(get_db), storage: StorageBackend = Depends(get_storage),
    user: User = Depends(require_role(*WRITERS)), s: Settings = Depends(get_settings),
):
    if not limiter().hit(f"upload:{user.id}", s.upload_rate_limit_per_min):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Upload rate limit exceeded")
    try:
        res = ingest_upload(db, storage, owner_id=user.id, filename=file.filename or "unnamed", stream=file.file,
                            settings=s)
        db.commit()
    except (ValidationError, ProcessingError) as exc:
        db.rollback()
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if exc.code == "file_too_large" else 422
        raise HTTPException(code, f"{exc.code}: {exc.message}") from None
    _publish(db)
    return UploadOut(document=DocumentOut.of(res.document), job_id=res.root_job_id, duplicate_of=res.duplicate_of)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    status_: str | None = Query(None, alias="status"), q: str | None = None, limit: int = Query(50, le=200),
    offset: int = 0, db: Session = Depends(get_db), user: User = Depends(current_user),
):
    stmt = select(Document).where(Document.deleted_at.is_(None))
    if user.role not in PRIVILEGED:
        stmt = stmt.where(Document.owner_id == user.id)
    if status_:
        try:
            stmt = stmt.where(Document.status == DocumentStatus(status_))
        except ValueError:
            raise HTTPException(422, "Unknown status") from None
    if q:
        stmt = stmt.where(Document.original_filename.ilike(f"%{q.replace('%', '').replace('_', '')}%"))
    rows = db.scalars(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return [DocumentOut.of(d) for d in rows]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return DocumentOut.of(get_document_for(db, user, document_id))


@router.get("/documents/{document_id}/progress")
def document_progress(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_document_for(db, user, document_id)
    return orch.progress(db, document_id)


@router.get("/documents/{document_id}/jobs", response_model=list[JobOut])
def document_jobs(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_document_for(db, user, document_id)
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.document_id == document_id)
                      .order_by(ProcessingJob.created_at)).all()
    return [JobOut.of(j) for j in jobs]


@router.get("/documents/{document_id}/history")
def document_history(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_document_for(db, user, document_id)
    rows = db.scalars(select(ProcessingHistory).where(ProcessingHistory.document_id == document_id)
                      .order_by(ProcessingHistory.created_at)).all()
    return [{"at": r.created_at, "event": r.event, "actor": r.actor, "job_id": r.job_id, "detail": r.detail}
            for r in rows]


@router.get("/documents/{document_id}/file")
def download(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user),
             storage: StorageBackend = Depends(get_storage)):
    """Authenticated streaming only: files are never exposed through a public URL."""
    doc = get_document_for(db, user, document_id)
    return FileResponse(storage.path(doc.storage_key), media_type=doc.mime_type, filename=doc.original_filename,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db),
                    user: User = Depends(require_role(UserRole.admin))) -> None:
    doc = get_document_for(db, user, document_id)
    doc.deleted_at = func.now()
    orch.log_event(db, doc.id, "deleted", actor=str(user.id))
    db.commit()


def _job_for(db: Session, user: User, job_id: uuid.UUID) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    get_document_for(db, user, job.document_id)
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry(job_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*WRITERS))):
    job = _job_for(db, user, job_id)
    try:
        orch.retry_job(db, job, actor=str(user.id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    _publish(db)
    return JobOut.of(job)


@router.post("/jobs/{job_id}/cancel")
def cancel(
    job_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*WRITERS))
) -> dict[str, Any]:
    job = _job_for(db, user, job_id)
    n = orch.cancel_job(db, job, actor=str(user.id))
    db.commit()
    return {"cancelled_jobs": n}


@router.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """Dashboard counters, scoped to what the caller may see."""
    from app.models.base import JobStatus, ReviewStatus

    scope = [Document.deleted_at.is_(None)]
    if user.role not in PRIVILEGED:
        scope.append(Document.owner_id == user.id)
    by_status = {k.value: v for k, v in db.execute(
        select(Document.status, func.count()).where(*scope).group_by(Document.status))}
    pending = db.scalar(select(func.count()).select_from(Invoice).join(Document, Document.id == Invoice.document_id)
                        .where(*scope, Invoice.deleted_at.is_(None), Invoice.requires_review.is_(True),
                               Invoice.review_status == ReviewStatus.pending))
    failed_jobs = db.scalar(select(func.count()).select_from(ProcessingJob)
                            .join(Document, Document.id == ProcessingJob.document_id)
                            .where(*scope, ProcessingJob.status.in_([JobStatus.failed, JobStatus.dead])))
    return {"documents": sum(by_status.values()), "by_status": by_status, "invoices_pending_review": pending,
            "failed_jobs": failed_jobs}


@router.get("/documents/{document_id}/pages")
def document_pages(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    get_document_for(db, user, document_id)
    rows = db.scalars(select(DocumentPage).where(DocumentPage.document_id == document_id)
                      .order_by(DocumentPage.page_number)).all()
    return [{"page": p.page_number, "kind": p.kind.value, "label": p.label, "status": p.status,
             "ocr_confidence": p.ocr_confidence, "text_chars": p.text_chars, "error": p.error} for p in rows]


@router.get("/documents/{document_id}/pages/{page}/text")
def page_text(document_id: uuid.UUID, page: int, db: Session = Depends(get_db),
              user: User = Depends(current_user)) -> dict[str, Any]:
    get_document_for(db, user, document_id)
    rows = db.scalars(select(ExtractedText).where(ExtractedText.document_id == document_id,
                                                  ExtractedText.page_number == page)).all()
    if not rows:
        raise HTTPException(404, "No text for this page")
    return {"page": page, "text": "\n".join(r.text for r in rows), "source": rows[0].source,
            "ocr_confidence": rows[0].ocr_confidence}
