from __future__ import annotations

import logging
from uuid import UUID

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.onedrive.service import recover_stale_submissions, run_onedrive_fetch

logger = logging.getLogger(__name__)


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


@celery_app.task(name="marriage_ocr_api.onedrive.recover_stale_submissions", bind=False)
def recover_stale_submissions_task() -> int:
    """Periodic safety net mirroring jobs/tasks.py::recover_stale_jobs_task:
    a submission stuck in FETCHING because its worker was killed mid-run
    (OOM, container recreate) would otherwise sit there forever with no
    operator signal and no way to retry. Runs on the beat schedule
    configured in celery_app.py.
    """
    settings = get_settings()
    session_factory = get_session_factory(settings)
    # Extra margin over the OCR job threshold: a submission's run covers a
    # fetch plus N per-file classify calls, not just one OCR run.
    stale_after_seconds = settings.ocr_timeout_seconds + 900
    with session_factory() as session:
        recovered = recover_stale_submissions(session, stale_after_seconds)
        session.commit()
    if recovered:
        logger.warning("Recovered %s stale FETCHING onedrive submission(s) abandoned by a crashed worker", recovered)
    return recovered
