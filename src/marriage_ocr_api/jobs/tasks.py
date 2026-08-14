from __future__ import annotations

from uuid import UUID

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.jobs.processing import process_ocr_job
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner


@celery_app.task(name="marriage_ocr_api.jobs.run_ocr_job", bind=False)
def run_ocr_job(job_id: str) -> None:
    settings = get_settings()
    session_factory = get_session_factory(settings)
    runner = SubprocessOCRRunner(settings)
    process_ocr_job(UUID(job_id), settings, session_factory, runner)
