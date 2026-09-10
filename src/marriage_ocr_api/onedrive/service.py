from __future__ import annotations

import contextlib
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.document_ingest import create_document_page_jobs, document_paths, split_page_count
from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.batches.repositories import create_document, recompute_batch_status, recompute_document_status
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories as job_repositories
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.runner import sanitize_stderr_text
from marriage_ocr_api.jobs.service import JobExecutorProtocol
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.onedrive import repositories
from marriage_ocr_api.onedrive.classification import CLASSIFICATION_TO_DOCUMENT_TYPE, SUPPORTED_EXTENSIONS
from marriage_ocr_api.onedrive.local_intake import save_local_file
from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.response_models import OneDriveSubmissionError, OneDriveSubmissionResponse, SkippedFile
from marriage_ocr_api.onedrive.runner import ClassifyError, OneDriveFetchError, OneDriveFetchRunner
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus
from marriage_ocr_api.storage.factory import get_storage_service
from marriage_ocr_api.storage.local import UploadValidationError

logger = logging.getLogger(__name__)

# Matches OneDriveSubmission.error_message's column width (String(1000)).
# fetch_public's stderr can be an entire Rich-rendered traceback panel from
# marriage-ocr's CLI (thousands of characters) -- writing that in raw
# exceeds the column and makes the UPDATE itself fail, which previously left
# the submission stuck at FETCHING forever with no operator-visible error at
# all (mark_failed's own commit failing, swallowed by
# _mark_submission_failed_safe's except-and-log).
_ERROR_MESSAGE_LIMIT = 1000

# Rich (marriage-ocr CLI's uncaught-exception renderer) always closes its
# boxed traceback panel with a border line like this before printing a
# plain, undecorated "ExceptionType: message" summary after it -- which the
# console may itself wrap across several physical lines if it's long.
_BOX_BOTTOM_BORDER_RE = re.compile(r"^[╰└][─\s]*[╯┘]?\s*$")


def _summarize_error_text(raw: str, limit: int) -> str:
    """Pulls out Rich's plain summary after the boxed panel (rejoining any
    lines the console wrapped it across) instead of storing -- and
    truncating -- the whole panel, which would otherwise save box-drawing
    art cut off mid-character as the visible error message."""
    cleaned = sanitize_stderr_text(raw, limit * 4)
    lines = cleaned.splitlines()
    border_indexes = [i for i, line in enumerate(lines) if _BOX_BOTTOM_BORDER_RE.match(line.strip())]
    tail_lines = lines[border_indexes[-1] + 1 :] if border_indexes else lines
    summary = " ".join(line.strip() for line in tail_lines if line.strip())
    if summary:
        return summary[:limit]
    return cleaned[-limit:]


class OneDriveExecutorProtocol(Protocol):
    def submit(self, submission_id: UUID) -> object: ...


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_or_create_submission(session: Session, *, batch_id: UUID, url: str) -> tuple[OneDriveSubmission, bool]:
    """Look up `url` globally (not scoped to batch_id) first -- a repeat POST
    of a URL already submitted, to this batch or any other, returns that
    submission's current state unchanged and triggers no new fetch. This
    look-up is just the fast path; the DB's own UNIQUE constraint on `url`
    (onedrive/models.py) is what actually stops a duplicate row from two
    identical POSTs racing past it -- handled below via IntegrityError.
    """
    existing = repositories.get_submission_by_url(session, url)
    if existing is not None:
        return existing, False

    try:
        submission = repositories.create_submission(session, batch_id=batch_id, url=url)
        session.commit()
        return submission, True
    except IntegrityError:
        session.rollback()
        existing = repositories.get_submission_by_url(session, url)
        if existing is None:
            raise
        return existing, False


def build_submission_response(submission: OneDriveSubmission) -> OneDriveSubmissionResponse:
    error = None
    if submission.error_code or submission.error_message:
        error = OneDriveSubmissionError(
            code=submission.error_code or "INTERNAL_ERROR",
            message=submission.error_message or "",
        )
    skipped_files = (
        [SkippedFile(**item) for item in submission.skipped_files] if submission.skipped_files else None
    )
    return OneDriveSubmissionResponse(
        id=submission.id,
        batch_id=submission.batch_id,
        url=submission.url,
        status=OneDriveSubmissionStatus(submission.status),
        skipped_files=skipped_files,
        created_at=submission.created_at,
        updated_at=submission.updated_at,
        fetched_at=submission.fetched_at,
        error=error,
    )


def _mark_submission_failed_safe(
    session_factory: sessionmaker[Session] | SessionFactory,
    submission_id: UUID,
    error_code: str,
    message: str,
) -> None:
    with session_factory() as session:
        try:
            repositories.mark_failed(session, submission_id, error_code=error_code, error_message=message)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to mark onedrive submission %s as failed", submission_id)


def _ingest_one_file(
    session: Session,
    settings: Settings,
    job_executor: JobExecutorProtocol,
    *,
    batch_id: UUID,
    submission_id: UUID,
    source_path: Path,
    document_type: DocumentType,
) -> UUID:
    document_id = uuid4()
    paths = document_paths(settings.storage_root, batch_id, document_id, source_path.suffix or ".pdf")
    original_filename = source_path.name
    try:
        stored = save_local_file(source_path, paths, settings)
        if settings.storage_backend == "s3":
            local_path = settings.storage_root.resolve() / stored.input_relative_path
            get_storage_service(settings).put_file(local_path, stored.input_relative_path)

        page_split_count = split_page_count(document_type, stored.content_type, paths.input_source_path)
        document = create_document(
            session,
            id=document_id,
            batch_id=batch_id,
            original_filename=original_filename,
            safe_filename=stored.stored_filename,
            media_type=stored.content_type,
            size_bytes=stored.file_size_bytes,
            sha256=stored.sha256,
            storage_key=stored.input_relative_path,
            document_type=document_type,
            page_count=page_split_count,
        )
        document.onedrive_submission_id = submission_id

        if page_split_count is not None:
            job_ids = create_document_page_jobs(
                session,
                settings,
                batch_id=batch_id,
                document=document,
                input_path=paths.input_source_path,
                page_count=page_split_count,
                document_type=document_type,
            )
        else:
            job_id = uuid4()
            job_repositories.create_job(
                session,
                id=job_id,
                batch_id=batch_id,
                document_id=document.id,
                status=JobStatus.PENDING,
                document_type=document_type,
                original_filename=original_filename,
                stored_filename=stored.stored_filename,
                content_type=stored.content_type,
                file_size_bytes=stored.file_size_bytes,
                input_relative_path=stored.input_relative_path,
                debug_relative_path=paths.debug_relative_path,
                stdout_log_relative_path=paths.stdout_log_relative_path,
                stderr_log_relative_path=paths.stderr_log_relative_path,
                ocr_git_ref=settings.marriage_ocr_git_ref,
            )
            job_ids = [job_id]
        session.commit()
    except Exception:
        session.rollback()
        shutil.rmtree(paths.job_root, ignore_errors=True)
        raise

    # Only remove the OneDrive-staged original now that the Document/Job
    # rows are durably committed -- `save_local_file` copies rather than
    # moves it precisely so it survives if this function had raised above.
    # Doing the delete here (not there) means a crash between "committed"
    # and "this line" is the only window where a retry could re-ingest an
    # already-ingested file; leaving the copy around indefinitely (i.e.
    # never deleting it here) would instead re-ingest -- and duplicate --
    # every successfully-ingested file on every future retry, which is
    # worse. Best-effort: a failure to remove it doesn't undo the ingest
    # that already succeeded, just leaves a harmless leftover file that the
    # submission's final cleanup (run_onedrive_fetch's shutil.rmtree of the
    # whole download directory) will still catch once the submission
    # completes normally.
    try:
        source_path.unlink()
    except OSError:
        logger.warning(
            "Ingested %s successfully but could not remove its OneDrive-staged "
            "original at %s",
            original_filename,
            source_path,
        )

    submission_failures = 0
    for pending_job_id in job_ids:
        try:
            job_executor.submit(pending_job_id)
        except Exception:
            submission_failures += 1
            logger.exception("failed to submit OCR job %s for document %s", pending_job_id, document_id)
            job_repositories.mark_failed(
                session,
                pending_job_id,
                "INTERNAL_PROCESSING_ERROR",
                "The OCR job could not be submitted for background processing.",
                _utcnow(),
            )
    if submission_failures:
        recompute_document_status(session, document_id)
    session.commit()
    return document_id


def run_onedrive_fetch(
    submission_id: UUID,
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    job_executor: JobExecutorProtocol,
    runner: OneDriveFetchRunner | None = None,
) -> None:
    """Fetch one OneDrive submission's link, classify every downloaded file,
    and route each routable file into the normal Document/OCRJob pipeline --
    the OneDrive counterpart of jobs/processing.py::process_ocr_job. Shared
    by the in-process executor and the Celery worker task.
    """
    runner = runner or OneDriveFetchRunner(settings)
    try:
        with session_factory() as session:
            submission = repositories.get_submission(session, submission_id)
            if submission is None:
                logger.error("onedrive submission %s not found", submission_id)
                return
            batch_id = submission.batch_id
            url = submission.url
            repositories.mark_fetching(session, submission_id)
            session.commit()

        dest_dir = settings.storage_root.resolve() / "onedrive" / str(submission_id) / "downloaded"
        try:
            runner.fetch_public(url, dest_dir)
        except OneDriveFetchError as exc:
            message = _summarize_error_text(exc.stderr.strip() or str(exc), _ERROR_MESSAGE_LIMIT)
            _mark_submission_failed_safe(session_factory, submission_id, "ONEDRIVE_FETCH_FAILED", message)
            logger.info("onedrive fetch failed for submission %s: %s", submission_id, message)
            return

        files = sorted(
            path for path in dest_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )

        skipped_files: list[dict[str, str]] = []
        with session_factory() as session:
            for file_path in files:
                try:
                    classification = runner.classify(file_path)
                except ClassifyError:
                    logger.exception("classify failed for %s (submission %s)", file_path, submission_id)
                    skipped_files.append({"filename": file_path.name, "status": "CLASSIFY_FAILED"})
                    continue

                if classification.status != "ROUTABLE" or classification.record_type is None:
                    skipped_files.append({"filename": file_path.name, "status": classification.status})
                    continue

                route_key = (classification.doc_type, classification.record_type, classification.layout_variant)
                document_type = CLASSIFICATION_TO_DOCUMENT_TYPE.get(route_key)
                if document_type is None:
                    skipped_files.append({"filename": file_path.name, "status": "UNKNOWN_ROUTABLE_COMBINATION"})
                    continue

                try:
                    _ingest_one_file(
                        session,
                        settings,
                        job_executor,
                        batch_id=batch_id,
                        submission_id=submission_id,
                        source_path=file_path,
                        document_type=document_type,
                    )
                except UploadValidationError as exc:
                    skipped_files.append({"filename": file_path.name, "status": exc.code})
                except Exception:
                    logger.exception("failed to ingest %s from onedrive submission %s", file_path, submission_id)
                    skipped_files.append({"filename": file_path.name, "status": "INGEST_FAILED"})

            repositories.mark_fetched(session, submission_id, skipped_files=skipped_files)
            recompute_batch_status(session, batch_id)
            session.commit()
        shutil.rmtree(dest_dir, ignore_errors=True)
        logger.info("completed onedrive submission %s (%d skipped)", submission_id, len(skipped_files))
    except Exception:
        logger.exception("unexpected error processing onedrive submission %s", submission_id)
        _mark_submission_failed_safe(
            session_factory,
            submission_id,
            "INTERNAL_PROCESSING_ERROR",
            "The OneDrive submission encountered an internal processing error.",
        )


def recover_stale_submissions(session: Session, stale_after_seconds: float) -> int:
    """Periodic recovery for submissions abandoned by a crashed or killed
    worker mid-run (fetch or classify) -- the OneDrive counterpart of
    jobs/service.py::recover_stale_jobs. See
    onedrive/repositories.py::fail_stale_fetching_submissions.
    """
    failed = repositories.fail_stale_fetching_submissions(session, _utcnow(), stale_after_seconds)
    return len(failed)


def retry_submission(session: Session, submission_id: UUID, executor: OneDriveExecutorProtocol) -> OneDriveSubmission:
    """Re-run a FAILED submission's fetch+classify from scratch. Needed
    because get_or_create_submission's URL-uniqueness dedup means a plain
    repeat POST of the same link returns the existing (failed) row
    unchanged and triggers no new fetch -- this is the only way to actually
    retry it (mirrors jobs/service.py::retry_job).
    """
    submission = repositories.get_submission(session, submission_id)
    if submission is None:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", "OneDrive submission not found.")
    if submission.status != OneDriveSubmissionStatus.FAILED.value:
        raise ApiError(409, "SUBMISSION_NOT_FAILED", "Only a failed submission can be retried.")

    submission = repositories.mark_pending_for_retry(session, submission_id)
    session.commit()

    try:
        logger.info("resubmitting onedrive submission %s for retry", submission_id)
        executor.submit(submission_id)
    except Exception as exc:
        repositories.mark_failed(
            session,
            submission_id,
            error_code="INTERNAL_PROCESSING_ERROR",
            error_message="The OneDrive submission could not be resubmitted for background processing.",
        )
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to resubmit OneDrive submission.") from exc
    return submission


def delete_submission(session: Session, settings: Settings, submission_id: UUID) -> None:
    """Deletes a OneDrive submission and everything it caused to be
    ingested -- its documents, their OCR jobs, and any records (+
    revisions) those jobs produced -- plus their stored files. The batch
    itself, and anything ingested by its *other* submissions, are
    untouched. Irreversible; the caller (the API route) is the one place a
    confirmation should already have happened. Mirrors
    batches/service.py::delete_batch, scoped to one submission instead of
    a whole batch.
    """
    submission = repositories.get_submission(session, submission_id)
    if submission is None:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", f"OneDrive submission {submission_id} not found.")
    batch_id = submission.batch_id

    documents = list(
        session.execute(
            select(Document.id, Document.batch_id, Document.storage_key).where(
                Document.onedrive_submission_id == submission_id
            )
        )
    )
    document_ids = [row.id for row in documents]
    job_rows = (
        list(
            session.execute(
                select(OCRJob.id, OCRJob.output_relative_path).where(OCRJob.document_id.in_(document_ids))
            )
        )
        if document_ids
        else []
    )
    job_output_keys = [row.output_relative_path for row in job_rows if row.output_relative_path]

    repositories.delete_submission_row(session, submission_id)
    recompute_batch_status(session, batch_id)
    session.commit()

    if settings.storage_backend == "s3":
        storage = get_storage_service(settings)
        for key in (*(row.storage_key for row in documents), *job_output_keys):
            with contextlib.suppress(FileNotFoundError):
                storage.delete(key)

    # Local disk cleanup always runs, even under STORAGE_BACKEND=s3 -- debug
    # artifacts and log files are never pushed to S3, so they only ever
    # exist on local disk (same reasoning as delete_batch).
    storage_root = settings.storage_root.resolve()
    for row in documents:
        shutil.rmtree(storage_root / "batches" / str(row.batch_id) / "documents" / str(row.id), ignore_errors=True)
    # A split multi-page document's per-page jobs live under their own
    # storage_root/jobs/{job_id}/ tree, entirely separate from the
    # document's own directory above -- see batches/document_ingest.py.
    for row in job_rows:
        shutil.rmtree(storage_root / "jobs" / str(row.id), ignore_errors=True)
