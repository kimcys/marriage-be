from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import (
    JobTransitionError,
    count_jobs,
    create_job,
    fail_interrupted_jobs,
    get_job,
    list_jobs,
    mark_completed,
    mark_failed,
    mark_processing,
)
from marriage_ocr_api.jobs.status import JobStatus


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_create_and_get_job(session: Session) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614174000")
    created = create_job(
        session,
        id=job_id,
        status=JobStatus.PENDING,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/123/input/source.pdf",
        debug_relative_path="jobs/123/debug",
        stdout_log_relative_path="jobs/123/logs/stdout.log",
        stderr_log_relative_path="jobs/123/logs/stderr.log",
        ocr_git_ref="abc123",
    )

    assert created.id == job_id
    fetched = get_job(session, job_id)
    assert fetched is not None
    assert fetched.original_filename == "register.pdf"
    assert fetched.status == JobStatus.PENDING


def test_list_and_count_jobs_are_sorted_newest_first(session: Session) -> None:
    older_id = UUID("123e4567-e89b-12d3-a456-426614174001")
    newer_id = UUID("123e4567-e89b-12d3-a456-426614174002")
    older = create_job(
        session,
        id=older_id,
        status=JobStatus.PENDING,
        original_filename="older.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/older/input/source.pdf",
        debug_relative_path="jobs/older/debug",
        stdout_log_relative_path="jobs/older/logs/stdout.log",
        stderr_log_relative_path="jobs/older/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    newer = create_job(
        session,
        id=newer_id,
        status=JobStatus.COMPLETED,
        original_filename="newer.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=456,
        input_relative_path="jobs/newer/input/source.pdf",
        output_relative_path="jobs/newer/output/result.xlsx",
        debug_relative_path="jobs/newer/debug",
        stdout_log_relative_path="jobs/newer/logs/stdout.log",
        stderr_log_relative_path="jobs/newer/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.flush()
    session.refresh(older)
    session.refresh(newer)

    jobs = list_jobs(session, None, 20, 0)
    assert [job.id for job in jobs] == [newer_id, older_id]
    assert count_jobs(session, None) == 2
    assert count_jobs(session, JobStatus.COMPLETED) == 1


def test_status_transitions_are_enforced(session: Session) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614174010")
    create_job(
        session,
        id=job_id,
        status=JobStatus.PENDING,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/123/input/source.pdf",
        debug_relative_path="jobs/123/debug",
        stdout_log_relative_path="jobs/123/logs/stdout.log",
        stderr_log_relative_path="jobs/123/logs/stderr.log",
        ocr_git_ref="abc123",
    )

    mark_processing(session, job_id, datetime.now(UTC))
    mark_completed(session, job_id, "jobs/123/output/result.xlsx", datetime.now(UTC))

    with pytest.raises(JobTransitionError):
        mark_processing(session, job_id, datetime.now(UTC))

    with pytest.raises(JobTransitionError):
        mark_failed(session, job_id, "OCR_PROCESS_FAILED", "boom", datetime.now(UTC))


def test_fail_interrupted_jobs_marks_only_processing_jobs(session: Session) -> None:
    processing_id = UUID("123e4567-e89b-12d3-a456-426614174020")
    pending_id = UUID("123e4567-e89b-12d3-a456-426614174021")
    create_job(
        session,
        id=processing_id,
        status=JobStatus.PROCESSING,
        original_filename="processing.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/processing/input/source.pdf",
        debug_relative_path="jobs/processing/debug",
        stdout_log_relative_path="jobs/processing/logs/stdout.log",
        stderr_log_relative_path="jobs/processing/logs/stderr.log",
        ocr_git_ref="abc123",
        started_at=datetime.now(UTC),
    )
    create_job(
        session,
        id=pending_id,
        status=JobStatus.PENDING,
        original_filename="pending.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/pending/input/source.pdf",
        debug_relative_path="jobs/pending/debug",
        stdout_log_relative_path="jobs/pending/logs/stdout.log",
        stderr_log_relative_path="jobs/pending/logs/stderr.log",
        ocr_git_ref="abc123",
    )

    fail_interrupted_jobs(session, datetime.now(UTC))

    processing = get_job(session, processing_id)
    pending = get_job(session, pending_id)

    assert processing is not None
    assert processing.status == JobStatus.FAILED
    assert processing.error_code == "PROCESS_INTERRUPTED"
    assert pending is not None
    assert pending.status == JobStatus.PENDING
