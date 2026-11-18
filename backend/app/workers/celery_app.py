"""Celery application. Configuration is explicit about reliability: late acks, bounded prefetch, time limits."""

from celery import Celery

from app.core.config import get_settings

_s = get_settings()

app = Celery("docint", broker=_s.redis_url, include=["app.workers.tasks"])
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,  # a killed worker's job is redelivered; run_job is idempotent
    worker_prefetch_multiplier=1,
    task_soft_time_limit=_s.task_soft_time_limit_s,
    task_time_limit=_s.task_soft_time_limit_s + 60,
    task_default_queue="default",
    broker_transport_options={"visibility_timeout": _s.task_soft_time_limit_s * 2},
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "relay-outbox": {"task": "docint.relay_outbox", "schedule": 5.0},
        "reset-stuck-jobs": {"task": "docint.reset_stuck", "schedule": 300.0},
    },
)
