from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.records.repositories import (
    append_revision,
    count_records,
    create_record,
    get_record,
    list_records,
    list_revisions,
)
from marriage_ocr_api.records.status import RecordStatus


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def _job(session: Session) -> UUID:
    job_id = UUID("123e4567-e89b-12d3-a456-426614175300")
    create_job(
        session,
        id=job_id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/123/input/source.pdf",
        output_relative_path="jobs/123/output/result.xlsx",
        debug_relative_path="jobs/123/debug",
        stdout_log_relative_path="jobs/123/logs/stdout.log",
        stderr_log_relative_path="jobs/123/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.commit()
    return job_id


def test_create_get_and_list_records(session: Session) -> None:
    job_id = _job(session)
    base_time = datetime.now(UTC)
    older = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
        created_at=base_time - timedelta(minutes=1),
        updated_at=base_time - timedelta(minutes=1),
    )
    newer = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-2",
        field_values={"full_name": "Grace Hopper"},
        confidence=0.95,
        validation_issues=["missing surname"],
        created_at=base_time,
        updated_at=base_time,
    )
    session.commit()

    fetched = get_record(session, older.id)
    assert fetched is not None
    assert fetched.source_key == "page-1-row-1"
    assert fetched.status == RecordStatus.PENDING_REVIEW.value

    records = list_records(session, job_id=job_id, status=None, limit=20, offset=0)
    assert [record.id for record in records] == [newer.id, older.id]
    assert count_records(session, job_id=job_id, status=None) == 2


def test_append_revision_tracks_history(session: Session) -> None:
    job_id = _job(session)
    record = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )
    session.commit()

    append_revision(
        session,
        record_id=record.id,
        version=2,
        previous_values={"full_name": "Ada Lovelace"},
        new_values={"full_name": "Ada Byron"},
        reviewer="reviewer@example.com",
        note="corrected surname",
    )
    session.commit()

    revisions = list_revisions(session, record.id)
    assert len(revisions) == 1
    assert revisions[0].version == 2
    assert revisions[0].note == "corrected surname"
