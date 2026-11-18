"""Upload intake: store, validate, record, and enqueue — one transaction for DB rows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Document
from app.models.base import JobStage
from app.processors.base import ValidationError
from app.processors.registry import get_processor
from app.services import orchestration as orch
from app.services.storage import StorageBackend

MAX_FILENAME = 255


@dataclass(slots=True)
class IngestResult:
    document: Document
    root_job_id: uuid.UUID
    duplicate_of: list[uuid.UUID] = field(default_factory=list)  # same content already uploaded (informational)


def safe_display_name(filename: str) -> str:
    """Name kept for display only; it never touches the filesystem."""
    name = Path(filename.replace("\\", "/")).name.strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')[:MAX_FILENAME]
    return name or "unnamed"


def ingest_upload(
    session: Session, storage: StorageBackend, *, owner_id: uuid.UUID, filename: str, stream: BinaryIO,
    settings: Settings | None = None,
) -> IngestResult:
    """Validate and store an upload. Rejected files are removed from storage and leave no DB rows."""
    s = settings or get_settings()
    display = safe_display_name(filename)
    ext = Path(display).suffix.lower()
    key, sha, size = storage.save(stream, ext, s.max_upload_bytes)
    try:
        path = storage.path(key)
        processor = get_processor(path, s.max_upload_bytes)  # signature + content checks
        info = processor.inspect(path)
    except Exception:
        storage.delete(key)  # never keep a rejected file
        raise

    duplicates = list(session.scalars(
        select(Document.id).where(Document.sha256 == sha, Document.deleted_at.is_(None))
    ))
    doc = Document(
        owner_id=owner_id, original_filename=display, storage_key=key, sha256=sha, mime_type=info.mime_type,
        size_bytes=size, page_count=info.page_count, metadata_=info.metadata,
    )
    session.add(doc)
    session.flush()
    orch.log_event(session, doc.id, "uploaded", actor=str(owner_id), size=size, duplicates=len(duplicates))
    root = orch.create_job(session, doc, JobStage.extract_text, queue=orch.queue_for(ext), settings=s)
    if root is None:  # unreachable for a fresh document id, but never fail silently
        raise ValidationError("job_exists", "Processing job already exists")
    return IngestResult(document=doc, root_job_id=root.id, duplicate_of=duplicates)
