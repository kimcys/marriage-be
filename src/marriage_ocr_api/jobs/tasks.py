from __future__ import annotations

import logging
from uuid import UUID

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.jobs.processing import process_ocr_job
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.jobs.service import recover_stale_jobs

logger = logging.getLogger(__name__)


@celery_app.task(name="marriage_ocr_api.jobs.run_ocr_job", bind=False)
def run_ocr_job(job_id: str) -> None:
    settings = get_settings()
    session_factory = get_session_factory(settings)
    runner = SubprocessOCRRunner(settings)
    process_ocr_job(UUID(job_id), settings, session_factory, runner)


@celery_app.task(name="marriage_ocr_api.jobs.recover_stale_jobs", bind=False)
def recover_stale_jobs_task() -> int:
    """Periodic safety net: a job stuck in PROCESSING because its worker was
    OOM-killed or hard-killed mid-run would otherwise block its whole
    document/batch forever with no operator signal (see
    jobs/service.py::recover_stale_jobs). Runs on the beat schedule
    configured in celery_app.py, well past any real OCR run's timeout.
    """
    settings = get_settings()
    session_factory = get_session_factory(settings)
    # Stale threshold must exceed the real per-job timeout with real margin,
    # so a merely-slow (not abandoned) job is never falsely marked failed.
    stale_after_seconds = settings.ocr_timeout_seconds + 300
    with session_factory() as session:
        recovered = recover_stale_jobs(session, stale_after_seconds)
        session.commit()
    if recovered:
        logger.warning("Recovered %s stale PROCESSING job(s) abandoned by a crashed worker", recovered)
    return recovered
