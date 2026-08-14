from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.processing import process_ocr_job
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class JobExecutor:
    """In-process, single-threaded job executor.

    Used when JOB_EXECUTOR_BACKEND=thread_pool (the default, and what all
    existing tests exercise). Real deployments should set JOB_EXECUTOR_BACKEND=
    celery (see jobs/celery_executor.py) to get genuine multi-worker concurrency.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session] | SessionFactory,
        runner: SubprocessOCRRunner,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._runner = runner
        self._pool = ThreadPoolExecutor(max_workers=1)

    def submit(self, job_id: UUID) -> Future[None]:
        return self._pool.submit(process_ocr_job, job_id, self.settings, self._session_factory, self._runner)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
