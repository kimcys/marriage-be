from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus
from marriage_ocr_api.records.models import OCRRecord, RecordRevision


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_submission(session: Session, submission_id: UUID) -> OneDriveSubmission | None:
    return session.get(OneDriveSubmission, submission_id)


def get_submission_by_url(session: Session, url: str) -> OneDriveSubmission | None:
    return session.scalar(select(OneDriveSubmission).where(OneDriveSubmission.url == url))


def list_submissions(session: Session, *, batch_id: UUID, limit: int, offset: int) -> list[OneDriveSubmission]:
    stmt: Select[tuple[OneDriveSubmission]] = select(OneDriveSubmission).where(OneDriveSubmission.batch_id == batch_id)
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
    submission.fetch_attempts = 0
    submission.fetched_at = now
    submission.updated_at = now
    session.flush()
    return submission


def restore_fetched(session: Session, submission_id: UUID) -> OneDriveSubmission:
    """Puts a submission back to FETCHED after a skipped-files reclassify
    (see onedrive/service.py::reclassify_skipped_files) without touching
    skipped_files or fetched_at -- unlike mark_fetched, which replaces both
    at the end of a full fetch."""
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    submission.status = OneDriveSubmissionStatus.FETCHED.value
    submission.updated_at = utcnow()
    session.flush()
    return submission


def get_skipped_file(session: Session, submission_id: UUID, filename: str) -> dict[str, str] | None:
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None or not submission.skipped_files:
        return None
    return next((item for item in submission.skipped_files if item.get("filename") == filename), None)


def update_skipped_file(
    session: Session, submission_id: UUID, filename: str, changes: dict[str, str | None]
) -> OneDriveSubmission:
    """Merges `changes` into one skipped_files entry (a None value drops that
    key). Row-locked, like remove_skipped_file, so a background re-download
    finishing for one file can't clobber another file's concurrent update
    to the same JSON list."""
    submission = session.get(OneDriveSubmission, submission_id, with_for_update=True, populate_existing=True)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    updated = []
    for item in submission.skipped_files or []:
        if item.get("filename") == filename:
            merged: dict[str, str | None] = {**item, **changes}
            item = {key: value for key, value in merged.items() if value is not None}
        updated.append(item)
    submission.skipped_files = updated or None
    submission.updated_at = utcnow()
    session.flush()
    return submission


def list_submissions_with_skipped_files(session: Session) -> list[OneDriveSubmission]:
    stmt = select(OneDriveSubmission).where(OneDriveSubmission.skipped_files.is_not(None))
    return list(session.scalars(stmt))


def remove_skipped_file(session: Session, submission_id: UUID, filename: str) -> OneDriveSubmission:
    """Drops one entry from skipped_files once classify_skipped_file has
    turned it into a real Document/Job -- it's no longer "skipped", so its
    chip should stop showing on the submission."""
    submission = session.get(OneDriveSubmission, submission_id, with_for_update=True, populate_existing=True)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    remaining = [item for item in (submission.skipped_files or []) if item.get("filename") != filename]
    submission.skipped_files = remaining or None
    submission.updated_at = utcnow()
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


def mark_pending_for_retry(session: Session, submission_id: UUID) -> OneDriveSubmission:
    submission = session.get(OneDriveSubmission, submission_id)
    if submission is None:
        raise ValueError(f"onedrive submission {submission_id} does not exist")
    if submission.status != OneDriveSubmissionStatus.FAILED.value:
        raise ValueError(f"onedrive submission {submission_id} is not failed")
    submission.status = OneDriveSubmissionStatus.PENDING.value
    submission.error_code = None
    submission.error_message = None
    submission.fetched_at = None
    submission.updated_at = utcnow()
    session.flush()
    return submission


def delete_submission_row(session: Session, submission_id: UUID) -> None:
    """Explicit, application-level cascade -- mirrors
    batches/repositories.py::delete_batch_row. `Document.onedrive_submission_id`
    is ON DELETE SET NULL (a document normally survives its origin link
    disappearing), but deleting the submission itself should take every
    document/job/record it produced with it rather than leaving them
    behind pointing at nothing -- and, same as delete_batch_row, this can't
    rely on the DB enforcing that FK at all (SQLite, used by this test
    suite, doesn't by default).
    """
    document_ids = select(Document.id).where(Document.onedrive_submission_id == submission_id)
    job_ids = select(OCRJob.id).where(OCRJob.document_id.in_(document_ids))
    record_ids = select(OCRRecord.id).where(OCRRecord.job_id.in_(job_ids))
    session.execute(delete(RecordRevision).where(RecordRevision.record_id.in_(record_ids)))
    session.execute(delete(OCRRecord).where(OCRRecord.job_id.in_(job_ids)))
    session.execute(delete(OCRJob).where(OCRJob.document_id.in_(document_ids)))
    session.execute(delete(Document).where(Document.onedrive_submission_id == submission_id))
    session.execute(delete(OneDriveSubmission).where(OneDriveSubmission.id == submission_id))


def resume_or_fail_stale_fetching_submissions(
    session: Session, now: datetime, stale_after_seconds: float, max_attempts: int
) -> tuple[list[UUID], list[OneDriveSubmission]]:
    """FETCHING submissions whose run stopped heartbeating (see
    onedrive/service.py::_heartbeat) -- almost always a worker restarted
    mid-run by a deploy. Up to `max_attempts` times each is handed back to be
    re-run (it stays FETCHING, so nothing changes for the user); after that
    it's FAILED as before. Returns (ids to re-run, submissions failed)."""
    cutoff = now - timedelta(seconds=stale_after_seconds)
    stale = list(
        session.scalars(
            select(OneDriveSubmission).where(
                OneDriveSubmission.status == OneDriveSubmissionStatus.FETCHING.value,
                OneDriveSubmission.updated_at < cutoff,
            )
        )
    )
    resume: list[UUID] = []
    failed: list[OneDriveSubmission] = []
    for submission in stale:
        if submission.fetch_attempts < max_attempts:
            submission.fetch_attempts += 1
            # PENDING, not FETCHING, until the resumed run actually starts
            # (claim_for_fetch): its task may wait in the queue behind other
            # long fetches, with no heartbeat, and must not look interrupted
            # again meanwhile.
            submission.status = OneDriveSubmissionStatus.PENDING.value
            submission.updated_at = now
            resume.append(submission.id)
        else:
            submission.status = OneDriveSubmissionStatus.FAILED.value
            submission.error_code = "PROCESSING_INTERRUPTED"
            submission.error_message = (
                "Processing was interrupted repeatedly and did not complete. Retry this link to try again."
            )
            submission.fetched_at = now
            submission.updated_at = now
            failed.append(submission)
    session.flush()
    return resume, failed


def claim_for_fetch(session: Session, submission_id: UUID, now: datetime, stale_after_seconds: float) -> bool:
    """Atomically move a submission to FETCHING for the run that's starting.
    Only a PENDING submission, or a FETCHING one whose run stopped
    heartbeating, can be claimed -- so a duplicate task (a re-queued resume,
    a broker redelivery) exits instead of running the same link twice, and a
    finished submission is never re-fetched by a stray task."""
    cutoff = now - timedelta(seconds=stale_after_seconds)
    result = session.execute(
        update(OneDriveSubmission)
        .where(
            OneDriveSubmission.id == submission_id,
            or_(
                OneDriveSubmission.status == OneDriveSubmissionStatus.PENDING.value,
                and_(
                    OneDriveSubmission.status == OneDriveSubmissionStatus.FETCHING.value,
                    OneDriveSubmission.updated_at < cutoff,
                ),
            ),
        )
        .values(status=OneDriveSubmissionStatus.FETCHING.value, updated_at=now)
    )
    session.flush()
    return bool(getattr(result, "rowcount", 0))


def requeue_lost_pending_submissions(session: Session, now: datetime, pending_after_seconds: float) -> list[UUID]:
    """PENDING submissions whose task never started -- e.g. a worker that had
    already received it was restarted. Re-queuing one whose task is merely
    waiting is harmless: whichever copy starts first claims it
    (claim_for_fetch) and the other exits. updated_at is bumped so each is
    re-queued at most once per `pending_after_seconds`."""
    cutoff = now - timedelta(seconds=pending_after_seconds)
    submissions = list(
        session.scalars(
            select(OneDriveSubmission).where(
                OneDriveSubmission.status == OneDriveSubmissionStatus.PENDING.value,
                OneDriveSubmission.updated_at < cutoff,
            )
        )
    )
    for submission in submissions:
        submission.updated_at = now
    session.flush()
    return [submission.id for submission in submissions]


def fail_stale_fetching_submissions(
    session: Session, now: datetime, stale_after_seconds: float
) -> list[OneDriveSubmission]:
    """Fail FETCHING submissions that have been running far longer than any
    real fetch+classify run should take -- the OneDrive counterpart of
    db/repositories.py::fail_stale_processing_jobs. This model has no
    started_at column; mark_fetching already bumps updated_at the moment a
    submission enters FETCHING, so that's the timestamp staleness is judged
    against.

    Without this, a submission whose worker was killed mid-run (e.g. a
    container recreate, exactly what left one submission stuck this way
    once already) sits at FETCHING forever with no operator-visible error
    and no way to retry it -- get_or_create_submission's URL dedup returns
    the same stuck row unchanged for any repeat POST of that link.
    """
    cutoff = now - timedelta(seconds=stale_after_seconds)
    submissions = list(
        session.scalars(
            select(OneDriveSubmission).where(
                OneDriveSubmission.status == OneDriveSubmissionStatus.FETCHING.value,
                OneDriveSubmission.updated_at < cutoff,
            )
        )
    )
    for submission in submissions:
        submission.status = OneDriveSubmissionStatus.FAILED.value
        submission.error_code = "PROCESSING_INTERRUPTED"
        submission.error_message = "Processing was interrupted and did not complete. Retry this link to try again."
        submission.fetched_at = now
        submission.updated_at = now
    session.flush()
    return submissions
