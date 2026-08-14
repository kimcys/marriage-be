from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import openpyxl
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job, get_job
from marriage_ocr_api.jobs.runner import OCRRunRequest, OCRRunResult
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.main import create_app


def _engine() -> Engine:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_startup_recovery_marks_processing_jobs_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614174500")
    create_job(
        session,
        id=job_id,
        status=JobStatus.PROCESSING,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
        ocr_git_ref="abc123",
        started_at=datetime.now(UTC),
    )
    session.commit()
    session.close()

    class FakeRunner:
        def __init__(self, settings):
            self.settings = settings

    class FakeExecutor:
        def __init__(self, settings, session_factory_arg, runner):
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    monkeypatch.setattr("marriage_ocr_api.main.get_session_factory", lambda settings: session_factory)
    monkeypatch.setattr("marriage_ocr_api.main.SubprocessOCRRunner", FakeRunner)
    monkeypatch.setattr("marriage_ocr_api.main.JobExecutor", FakeExecutor)

    with TestClient(create_app(Settings(storage_root=tmp_path))):
        pass

    check_session = session_factory()
    job = get_job(check_session, job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PROCESS_INTERRUPTED"
    assert job.error_message == "OCR processing was interrupted by an application restart."
    check_session.close()


def test_executor_processes_job_with_fresh_session(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614174501")
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
        def run(self, request: OCRRunRequest) -> OCRRunResult:
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            workbook = openpyxl.Workbook()
            workbook.active.append(["full_name", "Confidence"])
            workbook.active.append(["Ada Lovelace", 0.97])
            workbook.save(request.output_path)
            return OCRRunResult(return_code=0, timed_out=False, duration_seconds=0.01)

    from marriage_ocr_api.jobs.executor import JobExecutor

    executor = JobExecutor(Settings(storage_root=tmp_path), session_factory, FakeRunner())
    future = executor.submit(job_id)
    future.result(timeout=5)
    executor.shutdown()

    check_session = session_factory()
    job = get_job(check_session, job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.output_relative_path == f"jobs/{job_id}/output/result.xlsx"
    check_session.close()
