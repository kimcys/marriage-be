from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.repositories import create_batch, create_document, get_batch, recompute_batch_status
from marriage_ocr_api.batches.status import BatchStatus, DocumentStatus
from marriage_ocr_api.db.base import Base


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


def _batch_with_documents(session: Session, document_statuses: list[DocumentStatus]) -> UUID:
    batch = create_batch(session, name="batch", description=None, created_by=None)
    for status in document_statuses:
        create_document(
            session,
            batch_id=batch.id,
            original_filename="register.pdf",
            safe_filename="source.pdf",
            media_type="application/pdf",
            size_bytes=1,
            sha256="abc",
            storage_key=f"documents/{uuid4()}/input/source.pdf",
            status=status,
        )
    session.commit()
    return batch.id


@pytest.mark.parametrize(
    ("document_statuses", "expected"),
    [
        ([DocumentStatus.PROCESSED, DocumentStatus.PROCESSED], BatchStatus.COMPLETED),
        ([DocumentStatus.PROCESSED, DocumentStatus.FAILED], BatchStatus.FAILED),
        ([DocumentStatus.PROCESSED, DocumentStatus.PROCESSING], BatchStatus.PROCESSING),
        ([DocumentStatus.PROCESSED, DocumentStatus.CANCELLED], BatchStatus.CANCELLED),
        ([DocumentStatus.CANCELLED, DocumentStatus.CANCELLED], BatchStatus.CANCELLED),
        # A real failure still outranks a cancellation for the batch as a whole.
        ([DocumentStatus.CANCELLED, DocumentStatus.FAILED], BatchStatus.FAILED),
        ([DocumentStatus.UPLOADED], BatchStatus.DRAFT),
    ],
)
def test_recompute_batch_status_aggregates_document_statuses(
    session: Session,
    document_statuses: list[DocumentStatus],
    expected: BatchStatus,
) -> None:
    batch_id = _batch_with_documents(session, document_statuses)

    recompute_batch_status(session, batch_id)

    batch = get_batch(session, batch_id)
    assert batch is not None
    assert batch.status == expected.value


def test_recompute_batch_status_is_noop_for_unknown_batch(session: Session) -> None:
    recompute_batch_status(session, uuid4())
