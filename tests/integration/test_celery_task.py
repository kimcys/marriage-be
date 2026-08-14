from __future__ import annotations

from pathlib import Path
from uuid import UUID

import openpyxl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job, get_job
from marriage_ocr_api.jobs.runner import OCRRunRequest, OCRRunResult
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.jobs.tasks import run_ocr_job


def test_run_ocr_job_task_processes_job_synchronously(monkeypatch, tmp_path: Path) -> None:
    """Runs the real Celery task body via .apply() -- no broker/worker needed.

    This is the Celery-task counterpart to
    tests/unit/test_job_startup_recovery.py::test_executor_processes_job_with_fresh_session,
    proving the task delegates to the same process_ocr_job logic the thread-pool
    executor uses.
    """
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    session = session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614174600")
    create_job(
        session,
        id=job_id,
        status=JobStatus.PENDING,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.commit()
    session.close()

    class FakeRunner:
        def __init__(self, settings: Settings) -> None:
            pass

        def run(self, request: OCRRunRequest) -> OCRRunResult:
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            workbook = openpyxl.Workbook()
            workbook.active.append(["full_name", "Confidence"])
            workbook.active.append(["Grace Hopper", 0.95])
            workbook.save(request.output_path)
            return OCRRunResult(return_code=0, timed_out=False, duration_seconds=0.01)

    monkeypatch.setattr("marriage_ocr_api.jobs.tasks.get_settings", lambda: Settings(storage_root=tmp_path))
    monkeypatch.setattr("marriage_ocr_api.jobs.tasks.get_session_factory", lambda settings: session_factory)
    monkeypatch.setattr("marriage_ocr_api.jobs.tasks.SubprocessOCRRunner", FakeRunner)

    result = run_ocr_job.apply(args=[str(job_id)])
    result.get(timeout=5)

    check_session = session_factory()
    job = get_job(check_session, job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.output_relative_path == f"jobs/{job_id}/output/result.xlsx"
    check_session.close()
