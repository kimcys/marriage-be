from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.repositories import create_batch, create_document
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.exports.service import build_export_rows
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.records.repositories import create_record
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


def test_build_export_rows_merges_normalized_and_corrected_values(session: Session) -> None:
    batch = create_batch(session, name="Batch 1", description=None, created_by=UUID(int=1))
    document = create_document(
        session,
        batch_id=batch.id,
        original_filename="register.pdf",
        safe_filename="register.pdf",
        media_type="application/pdf",
        size_bytes=123,
        sha256="abc123",
        storage_key="batches/1/documents/1/input/register.pdf",
    )
    create_job(
        session,
        id=UUID(int=2),
        batch_id=batch.id,
        document_id=document.id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/1/input/source.pdf",
        output_relative_path="jobs/1/output/result.xlsx",
        debug_relative_path="jobs/1/debug",
        stdout_log_relative_path="jobs/1/logs/stdout.log",
        stderr_log_relative_path="jobs/1/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    create_record(
        session,
        job_id=UUID(int=2),
        batch_id=batch.id,
        document_id=document.id,
        source_key="row-1",
        source_page=1,
        source_record_index=0,
        field_values={"full_name": "Ada Lovelace", "confidence": 0.97},
        normalized_data={"full_name": "Ada Lovelace", "confidence": 0.97},
        corrected_data={"full_name": "Ada Byron"},
        confidence=0.97,
        validation_issues=[],
        status=RecordStatus.APPROVED,
        review_status="APPROVED",
    )
    create_record(
        session,
        job_id=UUID(int=2),
        batch_id=batch.id,
        document_id=document.id,
        source_key="row-2",
        source_page=1,
        source_record_index=1,
        field_values={"full_name": "Grace Hopper", "confidence": 0.95},
        normalized_data={"full_name": "Grace Hopper", "confidence": 0.95},
        confidence=0.95,
        validation_issues=[],
        status=RecordStatus.PENDING_REVIEW,
        review_status="PENDING",
    )
    session.commit()

    columns, rows = build_export_rows(session, batch.id, include_unreviewed=False)
    assert columns == ["full_name", "confidence"]
    assert rows == [{"full_name": "Ada Byron", "confidence": 0.97}]

    columns, rows = build_export_rows(session, batch.id, include_unreviewed=True)
    assert columns == ["full_name", "confidence"]
    assert rows == [
        {"full_name": "Ada Byron", "confidence": 0.97},
        {"full_name": "Grace Hopper", "confidence": 0.95},
    ]
