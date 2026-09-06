from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_submission(session: Session, submission_id: UUID) -> OneDriveSubmission | None:
    return session.get(OneDriveSubmission, submission_id)


def get_submission_by_url(session: Session, url: str) -> OneDriveSubmission | None:
    return session.scalar(select(OneDriveSubmission).where(OneDriveSubmission.url == url))


def list_submissions(session: Session, *, batch_id: UUID, limit: int, offset: int) -> list[OneDriveSubmission]:
    stmt: Select[tuple[OneDriveSubmission]] = select(OneDriveSubmission).where(
        OneDriveSubmission.batch_id == batch_id
    )
    stmt = stmt.order_by(OneDriveSubmission.created_at.desc(), OneDriveSubmission.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_submissions(session: Session, *, batch_id: UUID) -> int:
    stmt = select(func.count()).select_from(OneDriveSubmission).where(OneDriveSubmission.batch_id == batch_id)
    return int(session.scalar(stmt) or 0)


def create_submission(session: Session, *, batch_id: UUID, url: str) -> OneDriveSubmission:
    now = utcnow()
    submission = OneDriveSubmission(
        batch_id=batch_id,
        url=url,
        status=OneDriveSubmissionStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
    session.add(submission)
    session.flush()
    return submission


def mark_fetching(session: Session, submission_id: UUID) -> OneDriveSubmission:
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    submission.status = OneDriveSubmissionStatus.FETCHING.value
    submission.updated_at = utcnow()
    session.flush()
    return submission


def mark_fetched(
    session: Session,
    submission_id: UUID,
    *,
    skipped_files: list[dict[str, str]],
) -> OneDriveSubmission:
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    now = utcnow()
    submission.status = OneDriveSubmissionStatus.FETCHED.value
    submission.skipped_files = skipped_files or None
    submission.fetched_at = now
    submission.updated_at = now
    session.flush()
    return submission


def mark_failed(
    session: Session,
    submission_id: UUID,
    *,
    error_code: str,
    error_message: str,
) -> OneDriveSubmission:
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    now = utcnow()
    submission.status = OneDriveSubmissionStatus.FAILED.value
    submission.error_code = error_code
    submission.error_message = error_message
    submission.fetched_at = now
    submission.updated_at = now
    session.flush()
    return submission
