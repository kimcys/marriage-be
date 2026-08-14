from __future__ import annotations

from uuid import UUID

from marriage_ocr_api.jobs.celery_executor import CeleryJobExecutor


def test_celery_job_executor_dispatches_via_delay(monkeypatch) -> None:
    calls: list[str] = []

    def fake_delay(job_id: str) -> None:
        calls.append(job_id)

    monkeypatch.setattr("marriage_ocr_api.jobs.celery_executor.run_ocr_job.delay", fake_delay)

    executor = CeleryJobExecutor()
    job_id = UUID("123e4567-e89b-12d3-a456-426614174900")
    executor.submit(job_id)

    assert calls == [str(job_id)]

    executor.shutdown()  # no-op, must not raise
