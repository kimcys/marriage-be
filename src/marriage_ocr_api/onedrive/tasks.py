from __future__ import annotations

import logging
from uuid import UUID

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.onedrive.service import (
    recover_stale_submissions,
    run_onedrive_fetch,
    run_skipped_files_refetch,
)

logger = logging.getLogger(__name__)

_settings = get_settings()


@celery_app.task(
    name="marriage_ocr_api.onedrive.fetch_submission",
    bind=False,
    # Overrides celery_app.py's global task_time_limit/task_soft_time_limit
    # (sized for one OCR subprocess run) -- this task's own work is a fetch
    # plus N per-file classify calls plus ingest, which for a
    # several-thousand-file link can legitimately run far longer.
    time_limit=_settings.onedrive_fetch_timeout_seconds + 60,
    soft_time_limit=_settings.onedrive_fetch_timeout_seconds + 30,
)
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


@celery_app.task(
    name="marriage_ocr_api.onedrive.refetch_skipped_files",
    bind=False,
    # Same budget as a full fetch: even an --only pull may have to walk a
    # large share's whole folder tree (in a headless browser) to find them.
    time_limit=_settings.onedrive_fetch_timeout_seconds + 60,
    soft_time_limit=_settings.onedrive_fetch_timeout_seconds + 30,
)
def refetch_skipped_files(submission_id: str) -> None:
    """Re-downloads just the QUEUED skipped files of one submission -- see
    onedrive/service.py::run_skipped_files_refetch."""
    from marriage_ocr_api.jobs.celery_executor import CeleryJobExecutor

    settings = get_settings()
    session_factory = get_session_factory(settings)
    run_skipped_files_refetch(UUID(submission_id), settings, session_factory, CeleryJobExecutor())


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
    # Must stay above the fetch task's own time_limit (see tasks.py's
    # fetch_onedrive_submission) plus margin -- otherwise this would mark a
    # submission stale (and eligible for the operator to retry) while a
    # worker is still legitimately inside its allotted run.
    stale_after_seconds = settings.onedrive_fetch_timeout_seconds + 900
    with session_factory() as session:
        recovered = recover_stale_submissions(session, stale_after_seconds)
        session.commit()
    if recovered:
        logger.warning("Recovered %s stale FETCHING onedrive submission(s) abandoned by a crashed worker", recovered)
    return recovered
