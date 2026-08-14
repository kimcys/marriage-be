from __future__ import annotations

from uuid import UUID

from marriage_ocr_api.jobs.tasks import run_ocr_job


class CeleryJobExecutor:
    """Dispatches OCR jobs to Celery workers over the Valkey broker.

    Use this in real deployments (JOB_EXECUTOR_BACKEND=celery) so job processing
    is handled by independently scalable worker containers instead of a single
    in-process thread. See jobs/executor.py for the thread-pool alternative used
    by default/in tests.
    """

    def submit(self, job_id: UUID) -> None:
        run_ocr_job.delay(str(job_id))

    def shutdown(self) -> None:
        pass
