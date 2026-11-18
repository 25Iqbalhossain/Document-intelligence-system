"""Thin Celery wrappers; all logic lives in services.pipeline / services.orchestration."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.services import orchestration as orch
from app.services import pipeline
from app.services.storage import LocalStorage
from app.workers.celery_app import app


def celery_publisher(payload: dict[str, Any]) -> None:
    run_job.apply_async(args=[payload["job_id"]], queue=payload.get("queue", "default"))


@app.task(name="docint.run_job", bind=True, max_retries=None)
def run_job(self, job_id: str) -> str:
    s = get_settings()
    with get_session_factory()() as session:
        outcome = pipeline.run_job(session, LocalStorage(s.storage_root), job_id, settings=s)
        if outcome.status == "succeeded":
            orch.relay_outbox(session, celery_publisher)  # publish child batch jobs right away
    if outcome.status == "retry":
        raise self.retry(countdown=outcome.countdown)
    return outcome.status


@app.task(name="docint.relay_outbox")
def relay_outbox() -> int:
    with get_session_factory()() as session:
        return orch.relay_outbox(session, celery_publisher)


@app.task(name="docint.reset_stuck")
def reset_stuck() -> int:
    s = get_settings()
    with get_session_factory()() as session:
        n = orch.reset_stuck_running(session, older_than_s=s.task_soft_time_limit_s * 2)
        session.commit()
        return n
