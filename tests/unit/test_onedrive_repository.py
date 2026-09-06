from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.batches.repositories import create_batch
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.onedrive.repositories import (
    create_submission,
    get_submission,
    get_submission_by_url,
    mark_failed,
    mark_fetched,
    mark_fetching,
)
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus


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


def _batch_id(session: Session) -> UUID:
    return create_batch(session, name="Batch 1", description=None, created_by=None).id


def test_create_and_get_submission(session: Session) -> None:
    batch_id = _batch_id(session)
    submission = create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!abc")

    assert submission.status == OneDriveSubmissionStatus.PENDING.value
    fetched = get_submission(session, submission.id)
    assert fetched is not None
    assert fetched.url == "https://1drv.ms/f/s!abc"

    by_url = get_submission_by_url(session, "https://1drv.ms/f/s!abc")
    assert by_url is not None
    assert by_url.id == submission.id

    assert get_submission_by_url(session, "https://1drv.ms/f/s!does-not-exist") is None


def test_duplicate_url_violates_unique_constraint(session: Session) -> None:
    batch_id = _batch_id(session)
    create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!dup")

    with pytest.raises(IntegrityError):
        create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!dup")


def test_mark_fetching_then_fetched_with_skipped_files(session: Session) -> None:
    batch_id = _batch_id(session)
    submission = create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!xyz")

    mark_fetching(session, submission.id)
    assert get_submission(session, submission.id).status == OneDriveSubmissionStatus.FETCHING.value

    skipped = [{"filename": "jawi.jpg", "status": "SKIPPED_JAWI"}]
    updated = mark_fetched(session, submission.id, skipped_files=skipped)
    assert updated.status == OneDriveSubmissionStatus.FETCHED.value
    assert updated.skipped_files == skipped
    assert updated.fetched_at is not None


def test_mark_failed_records_error(session: Session) -> None:
    batch_id = _batch_id(session)
    submission = create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!fail")

    updated = mark_failed(session, submission.id, error_code="ONEDRIVE_FETCH_FAILED", error_message="sign-in required")
    assert updated.status == OneDriveSubmissionStatus.FAILED.value
    assert updated.error_code == "ONEDRIVE_FETCH_FAILED"
    assert updated.error_message == "sign-in required"
