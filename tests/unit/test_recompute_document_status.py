from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.repositories import create_batch, create_document, recompute_document_status
from marriage_ocr_api.batches.status import DocumentStatus
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus


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


def _document_with_jobs(session: Session, job_statuses: list[JobStatus]) -> UUID:
    batch = create_batch(session, name="batch", description=None, created_by=None)
    document = create_document(
        session,
        batch_id=batch.id,
        original_filename="register.pdf",
        safe_filename="source.pdf",
        media_type="application/pdf",
        size_bytes=1,
        sha256="abc",
        storage_key=f"documents/{uuid4()}/input/source.pdf",
    )
    for index, status in enumerate(job_statuses, start=1):
        create_job(
            session,
            id=uuid4(),
            batch_id=batch.id,
            document_id=document.id,
            status=status,
            page_number=index,
            original_filename="register.pdf",
            stored_filename=f"page-{index}.pdf",
            content_type="application/pdf",
            file_size_bytes=1,
            input_relative_path=f"jobs/page-{index}/input/source.pdf",
            debug_relative_path=f"jobs/page-{index}/debug",
            stdout_log_relative_path=f"jobs/page-{index}/logs/stdout.log",
            stderr_log_relative_path=f"jobs/page-{index}/logs/stderr.log",
            ocr_git_ref="abc123",
        )
    session.commit()
    return document.id


@pytest.mark.parametrize(
    ("job_statuses", "expected"),
    [
        ([JobStatus.PENDING, JobStatus.PENDING], DocumentStatus.PROCESSING),
        ([JobStatus.COMPLETED, JobStatus.PROCESSING], DocumentStatus.PROCESSING),
        ([JobStatus.COMPLETED, JobStatus.COMPLETED], DocumentStatus.PROCESSED),
        ([JobStatus.FAILED, JobStatus.FAILED], DocumentStatus.FAILED),
        ([JobStatus.COMPLETED, JobStatus.FAILED], DocumentStatus.PROCESSED),
        ([JobStatus.COMPLETED], DocumentStatus.PROCESSED),
    ],
)
def test_recompute_document_status_aggregates_job_statuses(
    session: Session,
    job_statuses: list[JobStatus],
    expected: DocumentStatus,
) -> None:
    document_id = _document_with_jobs(session, job_statuses)

    recompute_document_status(session, document_id)

    from marriage_ocr_api.batches.repositories import get_document

    document = get_document(session, document_id)
    assert document is not None
    assert document.status == expected.value


def test_recompute_document_status_is_noop_for_unknown_document(session: Session) -> None:
    recompute_document_status(session, uuid4())
