from __future__ import annotations

from celery import Celery

from marriage_ocr_api.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "marriage_ocr_api",
    broker=settings.valkey_url,
    backend=settings.valkey_url,
)
celery_app.conf.task_default_queue = "ocr_jobs"
celery_app.conf.task_track_started = True

from marriage_ocr_api.jobs import tasks  # noqa: E402,F401  (registers @celery_app.task defs)
