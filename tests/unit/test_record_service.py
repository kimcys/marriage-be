from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.records.importer import import_records_from_json
from marriage_ocr_api.records.repositories import create_record
from marriage_ocr_api.records.service import (
    RecordConflictError,
    apply_correction,
    approve_record,
    bulk_approve_records,
    reject_record,
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
    job_id = UUID("123e4567-e89b-12d3-a456-426614175400")
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


def test_import_records_from_json_is_idempotent(session: Session, tmp_path: Path) -> None:
    job_id = _job(session)
    payload = tmp_path / "ocr_records.json"
    payload.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_key": "page-1-row-1",
                        "field_values": {"full_name": "Ada Lovelace"},
                        "confidence": 0.97,
                        "validation_issues": [],
                    },
                    {
                        "source_key": "page-1-row-2",
                        "field_values": {"full_name": "Grace Hopper"},
                        "confidence": 0.95,
                        "validation_issues": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    created_first = import_records_from_json(session, job_id, payload)
    created_second = import_records_from_json(session, job_id, payload)

    assert created_first == 2
    assert created_second == 0


def test_apply_correction_rejects_stale_version(session: Session) -> None:
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

    with pytest.raises(RecordConflictError):
        apply_correction(
            session,
            record.id,
            expected_version=2,
            field_values={"full_name": "Ada Byron"},
            reviewer="reviewer@example.com",
            note="corrected surname",
        )


def test_approve_and_reject_records(session: Session) -> None:
    job_id = _job(session)
    first = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )
    second = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-2",
        field_values={"full_name": "Grace Hopper"},
        confidence=0.95,
        validation_issues=[],
    )
    session.commit()

    approved = approve_record(session, first.id, expected_version=1, reviewer="reviewer@example.com")
    rejected = reject_record(
        session,
        second.id,
        expected_version=1,
        reviewer="reviewer@example.com",
        reason="duplicate row",
    )

    assert approved.status == RecordStatus.APPROVED.value
    assert rejected.status == RecordStatus.REJECTED.value


def test_bulk_approve_records(session: Session) -> None:
    job_id = _job(session)
    first = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )
    second = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-2",
        field_values={"full_name": "Grace Hopper"},
        confidence=0.95,
        validation_issues=[],
    )
    session.commit()

    bulk_approved = bulk_approve_records(session, [first.id, second.id], reviewer="reviewer@example.com")

    assert [record.status for record in bulk_approved] == [RecordStatus.APPROVED.value, RecordStatus.APPROVED.value]
