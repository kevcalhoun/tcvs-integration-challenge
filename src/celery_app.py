"""Celery application configuration."""

from celery import Celery

from src.config import get_settings

settings = get_settings()

app = Celery(
    "tecovas_integration",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Celery configuration
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    worker_prefetch_multiplier=1,
)

# Auto-discover tasks
app.autodiscover_tasks(["src.tasks"])
