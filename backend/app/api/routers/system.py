from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import Document, Outbox, ProcessingJob

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}  # liveness only: no dependencies touched


@router.get("/ready")
def ready(db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    checks: dict[str, str] = {}
    try:
        db.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        import redis

        redis.Redis.from_url(s.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)) -> str:
    """Prometheus text format, computed from stored state. Expose on an internal network only."""
    lines = ["# TYPE docint_documents gauge"]
    for st, n in db.execute(select(Document.status, func.count()).where(Document.deleted_at.is_(None))
                            .group_by(Document.status)):
        lines.append(f'docint_documents{{status="{st.value}"}} {n}')
    lines.append("# TYPE docint_jobs gauge")
    for st, n in db.execute(select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)):
        lines.append(f'docint_jobs{{status="{st.value}"}} {n}')
    pending = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None)))
    lines += ["# TYPE docint_outbox_unpublished gauge", f"docint_outbox_unpublished {pending}"]
    return "\n".join(lines) + "\n"
