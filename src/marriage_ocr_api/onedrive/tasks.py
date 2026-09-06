from __future__ import annotations

from uuid import UUID

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.onedrive.service import run_onedrive_fetch


@celery_app.task(name="marriage_ocr_api.onedrive.fetch_submission", bind=False)
def fetch_onedrive_submission(submission_id: str) -> None:
    # Imported lazily (not at module level): jobs.celery_executor -> jobs.tasks
    # -> jobs.celery_app -> (bottom-of-file) this module -- a module-level
    # import here would be a circular import at app boot.
    from marriage_ocr_api.jobs.celery_executor import CeleryJobExecutor

    settings = get_settings()
    session_factory = get_session_factory(settings)
    # Each routable file's OCR job is submitted the same way a direct upload
    # would submit it -- via the same Celery-backed job executor, not a
    # bespoke path specific to OneDrive ingestion.
    job_executor = CeleryJobExecutor()
    run_onedrive_fetch(UUID(submission_id), settings, session_factory, job_executor)
