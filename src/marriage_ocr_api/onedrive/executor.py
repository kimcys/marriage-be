from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from uuid import UUID

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.service import JobExecutorProtocol
from marriage_ocr_api.onedrive.service import SessionFactory, run_onedrive_fetch


class OneDriveExecutor:
    """In-process, single-threaded executor for OneDrive fetch submissions.

    Used when JOB_EXECUTOR_BACKEND=thread_pool (the default, and what all
    existing tests exercise). See onedrive/celery_executor.py for the
    Celery-backed alternative used in real deployments.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory,
        job_executor: JobExecutorProtocol,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._job_executor = job_executor
        self._pool = ThreadPoolExecutor(max_workers=1)

    def submit(self, submission_id: UUID) -> Future[None]:
        return self._pool.submit(
            run_onedrive_fetch, submission_id, self.settings, self._session_factory, self._job_executor
        )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
