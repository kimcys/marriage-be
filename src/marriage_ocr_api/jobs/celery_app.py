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

# If a worker is killed mid-job (OOM, node eviction), a not-yet-acked task is
# redelivered to another worker instead of silently vanishing from the queue.
# The job row itself is recovered separately by the periodic
# recover_stale_jobs beat task below (redelivery alone doesn't reset a job
# already marked PROCESSING in the DB).
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
# Hard-kill a task that runs well past the OCR subprocess's own timeout,
# rather than letting a wedged worker process hold a slot forever.
celery_app.conf.task_time_limit = settings.ocr_timeout_seconds + 60
celery_app.conf.task_soft_time_limit = settings.ocr_timeout_seconds + 30

celery_app.conf.beat_schedule = {
    "recover-stale-ocr-jobs": {
        "task": "marriage_ocr_api.jobs.recover_stale_jobs",
        # Frequent enough that a crashed worker's job doesn't block its
        # document/batch for long, cheap enough (one indexed query when
        # nothing is stale) to run continuously in production.
        "schedule": 300.0,
    },
    "cleanup-stale-exports": {
        "task": "marriage_ocr_api.exports.cleanup_stale_exports",
        # Once a day is plenty for a retention window measured in weeks.
        "schedule": 86400.0,
    },
}

from marriage_ocr_api.jobs import tasks  # noqa: E402,F401  (registers @celery_app.task defs)
from marriage_ocr_api.exports import tasks as export_tasks  # noqa: E402,F401
