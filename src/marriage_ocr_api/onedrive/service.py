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
from marriage_ocr_api.batches.status import TYPED_DOCUMENT_TYPES, DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories as job_repositories
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.paths import page1_ocr_relative_path
from marriage_ocr_api.jobs.runner import sanitize_stderr_text
from marriage_ocr_api.jobs.service import JobExecutorProtocol
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.onedrive import repositories
from marriage_ocr_api.onedrive.classification import CLASSIFICATION_TO_DOCUMENT_TYPE, SUPPORTED_EXTENSIONS
from marriage_ocr_api.onedrive.local_intake import save_local_file
from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.response_models import OneDriveSubmissionError, OneDriveSubmissionResponse, SkippedFile
from marriage_ocr_api.onedrive.runner import Classification, ClassifyError, OneDriveFetchError, OneDriveFetchRunner
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

    def submit_refetch(self, submission_id: UUID) -> object: ...

    def submit_reclassify(self, submission_id: UUID) -> object: ...


# skipped_files entry keys for a missing file being re-downloaded on its own
# (see classify_skipped_file / run_skipped_files_refetch).
_REFETCH_STATUS = "refetch_status"
_REFETCH_DOCUMENT_TYPE = "refetch_document_type"
_REFETCH_ERROR = "refetch_error"
_REFETCH_UPDATED_AT = "refetch_updated_at"
_REFETCH_QUEUED = "QUEUED"
_REFETCH_IN_PROGRESS = "IN_PROGRESS"
_REFETCH_FAILED = "FAILED"

# Skip reasons worth re-running auto-classification for: CLASSIFY_FAILED is
# usually environmental (e.g. a worker that couldn't read the Google Vision
# key), and NEEDS_MANUAL_CLASSIFICATION can resolve via neighbour fallback
# once the pages around it classify (see reclassify_skipped_files).
# Jawi-only pages are skipped completely: never routed by neighbour
# fallback, never offered for manual classification, never reclassified.
_SKIPPED_JAWI = "SKIPPED_JAWI"
_RECLASSIFIABLE_STATUSES = frozenset({"CLASSIFY_FAILED", "NEEDS_MANUAL_CLASSIFICATION"})


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
    # `storage_key` (an internal on-disk path, see _record_skipped_file)
    # never leaves the server -- the frontend only needs to know whether
    # this file's bytes are still around to act on, not where.
    skipped_files = (
        [
            SkippedFile(
                filename=item["filename"],
                status=item["status"],
                classifiable=item["status"] != _SKIPPED_JAWI
                and item.get(_REFETCH_STATUS) not in (_REFETCH_QUEUED, _REFETCH_IN_PROGRESS),
                refetch_status=item.get(_REFETCH_STATUS),
                refetch_error=item.get(_REFETCH_ERROR),
            )
            for item in submission.skipped_files
        ]
        if submission.skipped_files
        else None
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


def _page_ocr_staging_path(settings: Settings, submission_id: UUID, index: int) -> Path:
    """Where classify saves one downloaded file's page-1 Vision result
    (typed PDFs only) until _ingest_one_file stores it next to the input."""
    return settings.storage_root.resolve() / "onedrive" / str(submission_id) / "page-ocr" / f"{index}.json"


def _store_page1_ocr(settings: Settings, source: Path, input_relative_path: str) -> None:
    relative_path = page1_ocr_relative_path(input_relative_path)
    dest = settings.storage_root.resolve() / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    if settings.storage_backend == "s3":
        get_storage_service(settings).put_file(dest, relative_path, content_type="application/json")


def _ingest_one_file(
    session: Session,
    settings: Settings,
    job_executor: JobExecutorProtocol,
    *,
    batch_id: UUID,
    submission_id: UUID,
    source_path: Path,
    document_type: DocumentType,
    page1_ocr_source: Path | None = None,
) -> UUID:
    document_id = uuid4()
    paths = document_paths(settings.storage_root, batch_id, document_id, source_path.suffix or ".pdf")
    original_filename = source_path.name
    try:
        stored = save_local_file(source_path, paths, settings)
        if settings.storage_backend == "s3":
            local_path = settings.storage_root.resolve() / stored.input_relative_path
            get_storage_service(settings).put_file(
                local_path, stored.input_relative_path, content_type=stored.content_type
            )
        if page1_ocr_source is not None and page1_ocr_source.is_file() and document_type in TYPED_DOCUMENT_TYPES:
            _store_page1_ocr(settings, page1_ocr_source, stored.input_relative_path)

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
            "Ingested %s successfully but could not remove its OneDrive-staged original at %s",
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


def _skipped_files_dir(settings: Settings, submission_id: UUID) -> Path:
    return settings.storage_root.resolve() / "onedrive" / str(submission_id) / "skipped"


def _record_skipped_file(
    settings: Settings,
    submission_id: UUID,
    skipped_files: list[dict[str, str]],
    file_path: Path,
    status: str,
) -> None:
    """Records a file classification/ingestion couldn't route, and -- unlike
    before this existed -- preserves its bytes in durable per-submission
    storage instead of leaving it in the temp download directory
    run_onedrive_fetch wipes once the run finishes. Without this, only the
    filename and status string would survive; nothing would be left for a
    human to actually act on via classify_skipped_file (POST .../skipped-
    files/classify) once NEEDS_MANUAL_CLASSIFICATION (or any other skip
    reason) is reported.
    """
    entry: dict[str, str] = {"filename": file_path.name, "status": status}
    try:
        skipped_dir = _skipped_files_dir(settings, submission_id)
        skipped_dir.mkdir(parents=True, exist_ok=True)
        dest = skipped_dir / file_path.name
        shutil.move(str(file_path), str(dest))
        storage_key = f"onedrive/{submission_id}/skipped/{file_path.name}"
        # Under STORAGE_BACKEND=s3 the worker (which runs this) and the API
        # (which later serves classify_skipped_file) don't share a disk --
        # production gives each its own volume -- so the bytes must also go
        # to object storage or the API will never find them.
        if settings.storage_backend == "s3":
            get_storage_service(settings).put_file(dest, storage_key)
        entry["storage_key"] = storage_key
    except Exception:
        logger.warning("could not preserve skipped file %s for submission %s", file_path, submission_id, exc_info=True)
    skipped_files.append(entry)


def _apply_neighbor_fallback(
    classified: list[tuple[Path, Classification]],
) -> list[tuple[Path, Classification]]:
    """A bound ledger book photographed page-by-page keeps one record_type/
    layout_variant throughout, but its title (what triage.py's classifier
    keys off) doesn't necessarily appear on every single page -- a
    continuation page deep in the same book, or one whose title band the
    photo simply didn't capture, comes back as doc_type="unknown"
    (NEEDS_MANUAL_CLASSIFICATION) even though a human looking at the whole
    batch in filename order would immediately recognise which book it
    belongs to. Confirmed on a real 154-photo client batch where this
    accounted for 26 of 43 originally-unroutable files, clustering in
    consecutive runs right next to confidently-classified neighbours from
    the same book.

    For each doc_type="unknown" file (`classified` must already be in
    filename order -- how a photographed ledger's pages are naturally
    numbered), look outward for the nearest ROUTABLE classification on each
    side within this same batch. Only inherit it when BOTH sides exist and
    agree on the exact same (doc_type, record_type, layout_variant) -- a
    batch boundary between two different books/record types, or a run of
    unknowns at the very start/end of the batch, is left as
    NEEDS_MANUAL_CLASSIFICATION rather than guessed at with no supporting
    evidence anywhere in the batch.
    """
    result = list(classified)
    for i, (file_path, classification) in enumerate(result):
        # Status, not just doc_type: a Jawi page usually reads doc_type="unknown"
        # too (no Latin title keyword matches), and must never be routed.
        if classification.doc_type != "unknown" or classification.status != "NEEDS_MANUAL_CLASSIFICATION":
            continue

        before = next((c for _, c in reversed(result[:i]) if c.status == "ROUTABLE"), None)
        after = next((c for _, c in result[i + 1 :] if c.status == "ROUTABLE"), None)
        if before is None or after is None:
            continue
        before_key = (before.doc_type, before.record_type, before.layout_variant)
        after_key = (after.doc_type, after.record_type, after.layout_variant)
        if before_key != after_key:
            continue

        logger.info(
            "onedrive classify: inheriting %s from neighbouring pages for %s (was NEEDS_MANUAL_CLASSIFICATION)",
            before_key,
            file_path.name,
        )
        result[i] = (
            file_path,
            Classification(
                doc_type=before.doc_type,
                record_type=before.record_type,
                layout_variant=before.layout_variant,
                status="ROUTABLE",
                config_path=before.config_path,
            ),
        )
    return result


def _route(classification: Classification) -> DocumentType | str:
    """The DocumentType a classification ingests as, or the skip status to
    record when it can't be routed."""
    if classification.status != "ROUTABLE" or classification.record_type is None:
        return classification.status
    route_key = (classification.doc_type, classification.record_type, classification.layout_variant)
    return CLASSIFICATION_TO_DOCUMENT_TYPE.get(route_key) or "UNKNOWN_ROUTABLE_COMBINATION"


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
            classified: list[tuple[Path, Classification]] = []
            page_ocr_by_file: dict[Path, Path] = {}
            for index, file_path in enumerate(files):
                page_ocr_by_file[file_path] = _page_ocr_staging_path(settings, submission_id, index)
                try:
                    classification = runner.classify(file_path, page_ocr_output=page_ocr_by_file[file_path])
                except ClassifyError as exc:
                    logger.exception(
                        "classify failed for %s (submission %s): %s",
                        file_path,
                        submission_id,
                        _summarize_error_text(exc.stderr.strip() or str(exc), _ERROR_MESSAGE_LIMIT),
                    )
                    _record_skipped_file(settings, submission_id, skipped_files, file_path, "CLASSIFY_FAILED")
                    continue
                classified.append((file_path, classification))

            classified = _apply_neighbor_fallback(classified)

            for file_path, classification in classified:
                document_type = _route(classification)
                if not isinstance(document_type, DocumentType):
                    _record_skipped_file(settings, submission_id, skipped_files, file_path, document_type)
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
                        page1_ocr_source=page_ocr_by_file.get(file_path),
                    )
                except UploadValidationError as exc:
                    _record_skipped_file(settings, submission_id, skipped_files, file_path, exc.code)
                except Exception:
                    logger.exception("failed to ingest %s from onedrive submission %s", file_path, submission_id)
                    _record_skipped_file(settings, submission_id, skipped_files, file_path, "INGEST_FAILED")

            repositories.mark_fetched(session, submission_id, skipped_files=skipped_files)
            recompute_batch_status(session, batch_id)
            session.commit()
        shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.rmtree(_page_ocr_staging_path(settings, submission_id, 0).parent, ignore_errors=True)
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
    return len(failed) + _fail_stale_refetches(session, stale_after_seconds)


def _fail_stale_refetches(session: Session, stale_after_seconds: float) -> int:
    """A skipped file left QUEUED/IN_PROGRESS by a re-download whose worker
    died would otherwise keep its Classify button disabled forever -- mark
    it FAILED so a reviewer can click Classify again."""
    now = _utcnow()
    recovered = 0
    for submission in repositories.list_submissions_with_skipped_files(session):
        for item in list(submission.skipped_files or []):
            if item.get(_REFETCH_STATUS) not in (_REFETCH_QUEUED, _REFETCH_IN_PROGRESS):
                continue
            updated_at = item.get(_REFETCH_UPDATED_AT)
            if updated_at and (now - datetime.fromisoformat(updated_at)).total_seconds() < stale_after_seconds:
                continue
            repositories.update_skipped_file(
                session,
                submission.id,
                item["filename"],
                {
                    _REFETCH_STATUS: _REFETCH_FAILED,
                    _REFETCH_DOCUMENT_TYPE: None,
                    _REFETCH_ERROR: "The re-download was interrupted. Click Classify to try again.",
                },
            )
            recovered += 1
    return recovered


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


def classify_skipped_file(
    session: Session,
    settings: Settings,
    job_executor: JobExecutorProtocol,
    submission_id: UUID,
    *,
    filename: str,
    document_type: DocumentType,
    onedrive_executor: OneDriveExecutorProtocol | None = None,
) -> OneDriveSubmissionResponse:
    """A human's override for a file the auto-classifier couldn't route
    (NEEDS_MANUAL_CLASSIFICATION and friends -- see _record_skipped_file):
    ingests it through the exact same path a confidently-auto-classified
    file already goes through (_ingest_one_file), just with the document
    type supplied directly instead of looked up from triage.py's
    keyword-matched guess. Raises ApiError for anything the caller should
    see as a 4xx (missing submission/file, or bytes no longer available).

    When the preserved bytes are gone (recorded before they were kept in
    object storage, or lost with a worker's disk), and an
    `onedrive_executor` is given, the file is instead queued to be
    re-downloaded -- on its own, not the whole share -- from the
    submission's link and ingested as `document_type` in the background
    (run_skipped_files_refetch). The returned entry then shows
    refetch_status=QUEUED until that finishes.
    """
    submission = repositories.get_submission(session, submission_id)
    if submission is None:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", "OneDrive submission not found.")

    if submission.status == OneDriveSubmissionStatus.FETCHING.value:
        raise ApiError(409, "SUBMISSION_BUSY", "This link's files are still being classified. Try again shortly.")

    entry = repositories.get_skipped_file(session, submission_id, filename)
    if entry is None:
        raise ApiError(404, "SKIPPED_FILE_NOT_FOUND", f"{filename} is not a skipped file on this submission.")

    if entry.get("status") == _SKIPPED_JAWI:
        raise ApiError(409, "SKIPPED_FILE_JAWI", f"{filename} is a Jawi page and is skipped from processing.")

    storage_key = entry.get("storage_key")
    source_path = settings.storage_root.resolve() / storage_key if storage_key else None
    if storage_key and source_path is not None and not source_path.is_file() and settings.storage_backend == "s3":
        # Preserved by the worker on its own disk; fetch the object-storage
        # copy (see _record_skipped_file) onto this process's disk.
        with contextlib.suppress(Exception):
            get_storage_service(settings).materialize(storage_key, source_path)
    if entry.get(_REFETCH_STATUS) in (_REFETCH_QUEUED, _REFETCH_IN_PROGRESS):
        raise ApiError(409, "SKIPPED_FILE_REFETCHING", f"{filename} is already being re-downloaded from OneDrive.")
    if (source_path is None or not source_path.is_file()) and onedrive_executor is not None:
        return _queue_skipped_file_refetch(session, onedrive_executor, submission_id, filename, document_type)
    if source_path is None or not source_path.is_file():
        raise ApiError(
            409,
            "SKIPPED_FILE_UNAVAILABLE",
            f"{filename}'s original file is no longer available and can't be classified.",
        )

    try:
        _ingest_one_file(
            session,
            settings,
            job_executor,
            batch_id=submission.batch_id,
            submission_id=submission_id,
            source_path=source_path,
            document_type=document_type,
        )
    except UploadValidationError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc

    submission = repositories.remove_skipped_file(session, submission_id, filename)
    recompute_batch_status(session, submission.batch_id)
    session.commit()
    if storage_key and settings.storage_backend == "s3":
        with contextlib.suppress(Exception):
            get_storage_service(settings).delete(storage_key)
    return build_submission_response(submission)


def _queue_skipped_file_refetch(
    session: Session,
    onedrive_executor: OneDriveExecutorProtocol,
    submission_id: UUID,
    filename: str,
    document_type: DocumentType,
) -> OneDriveSubmissionResponse:
    submission = repositories.update_skipped_file(
        session,
        submission_id,
        filename,
        {
            _REFETCH_STATUS: _REFETCH_QUEUED,
            _REFETCH_DOCUMENT_TYPE: document_type.value,
            _REFETCH_ERROR: None,
            _REFETCH_UPDATED_AT: _utcnow().isoformat(),
        },
    )
    session.commit()
    try:
        onedrive_executor.submit_refetch(submission_id)
    except Exception as exc:
        repositories.update_skipped_file(
            session,
            submission_id,
            filename,
            {
                _REFETCH_STATUS: _REFETCH_FAILED,
                _REFETCH_DOCUMENT_TYPE: None,
                _REFETCH_ERROR: "The re-download could not be queued for background processing.",
            },
        )
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", f"Failed to queue {filename} for re-download.") from exc
    logger.info("queued %s of onedrive submission %s for re-download", filename, submission_id)
    return build_submission_response(submission)


def _set_refetch_failed(
    session_factory: sessionmaker[Session] | SessionFactory,
    submission_id: UUID,
    filenames: list[str],
    message: str,
) -> None:
    with session_factory() as session:
        try:
            for filename in filenames:
                repositories.update_skipped_file(
                    session,
                    submission_id,
                    filename,
                    {_REFETCH_STATUS: _REFETCH_FAILED, _REFETCH_DOCUMENT_TYPE: None, _REFETCH_ERROR: message},
                )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to record re-download failure for onedrive submission %s", submission_id)


def run_skipped_files_refetch(
    submission_id: UUID,
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    job_executor: JobExecutorProtocol,
    runner: OneDriveFetchRunner | None = None,
) -> None:
    """Background half of classify_skipped_file's re-download fallback:
    claims every QUEUED skipped file on the submission, downloads *only
    those* from its OneDrive link (marriage-ocr's `fetch-public --only`),
    and ingests each as the document type the reviewer picked -- the same
    _ingest_one_file path any other file takes. Files queued while this
    runs are left QUEUED for the task their own click submitted.
    """
    runner = runner or OneDriveFetchRunner(settings)
    dest_dir = settings.storage_root.resolve() / "onedrive" / str(submission_id) / "refetch" / uuid4().hex
    claimed: dict[str, DocumentType] = {}
    try:
        with session_factory() as session:
            submission = repositories.get_submission(session, submission_id)
            if submission is None:
                return
            url = submission.url
            for item in submission.skipped_files or []:
                if item.get(_REFETCH_STATUS) == _REFETCH_QUEUED and item.get(_REFETCH_DOCUMENT_TYPE):
                    claimed[item["filename"]] = DocumentType(item[_REFETCH_DOCUMENT_TYPE])
            for filename in claimed:
                repositories.update_skipped_file(
                    session,
                    submission_id,
                    filename,
                    {_REFETCH_STATUS: _REFETCH_IN_PROGRESS, _REFETCH_UPDATED_AT: _utcnow().isoformat()},
                )
            session.commit()
        if not claimed:
            return

        try:
            runner.fetch_public(url, dest_dir, only=sorted(claimed))
        except OneDriveFetchError as exc:
            message = _summarize_error_text(exc.stderr.strip() or str(exc), _ERROR_MESSAGE_LIMIT)
            _set_refetch_failed(session_factory, submission_id, list(claimed), message)
            logger.info("onedrive re-download failed for submission %s: %s", submission_id, message)
            return

        downloaded: dict[str, Path] = {}
        for path in sorted(dest_dir.rglob("*")):
            if path.is_file():
                downloaded.setdefault(path.name.lower(), path)

        with session_factory() as session:
            submission = repositories.get_submission(session, submission_id)
            if submission is None:
                return
            batch_id = submission.batch_id
            for filename, document_type in claimed.items():
                entry = repositories.get_skipped_file(session, submission_id, filename)
                if entry is None:
                    continue  # classified or deleted some other way meanwhile
                source_path = downloaded.get(filename.lower())
                if source_path is None:
                    _set_refetch_failed(
                        session_factory, submission_id, [filename], f"{filename} is no longer in the OneDrive link."
                    )
                    continue
                try:
                    _ingest_one_file(
                        session,
                        settings,
                        job_executor,
                        batch_id=batch_id,
                        submission_id=submission_id,
                        source_path=source_path,
                        document_type=document_type,
                    )
                except UploadValidationError as exc:
                    _set_refetch_failed(session_factory, submission_id, [filename], exc.message)
                    continue
                except Exception:
                    logger.exception("failed to ingest re-downloaded %s (submission %s)", filename, submission_id)
                    _set_refetch_failed(
                        session_factory, submission_id, [filename], "The re-downloaded file could not be ingested."
                    )
                    continue
                repositories.remove_skipped_file(session, submission_id, filename)
                session.commit()
                with contextlib.suppress(Exception):
                    storage_key = entry.get("storage_key")
                    if storage_key and settings.storage_backend == "s3":
                        get_storage_service(settings).delete(storage_key)
            recompute_batch_status(session, batch_id)
            session.commit()
        logger.info("re-downloaded %d skipped file(s) for onedrive submission %s", len(claimed), submission_id)
    except Exception:
        logger.exception("unexpected error re-downloading skipped files for onedrive submission %s", submission_id)
        _set_refetch_failed(
            session_factory,
            submission_id,
            list(claimed),
            "The re-download encountered an internal processing error.",
        )
    finally:
        shutil.rmtree(dest_dir, ignore_errors=True)


def _is_reclassifiable(item: dict[str, str]) -> bool:
    return item.get("status") in _RECLASSIFIABLE_STATUSES and item.get(_REFETCH_STATUS) not in (
        _REFETCH_QUEUED,
        _REFETCH_IN_PROGRESS,
    )


def reclassify_skipped_files(
    session: Session, submission_id: UUID, executor: OneDriveExecutorProtocol
) -> OneDriveSubmission:
    """Queue a re-run of auto-classification over a FETCHED submission's
    CLASSIFY_FAILED / NEEDS_MANUAL_CLASSIFICATION skipped files (see
    run_skipped_files_reclassify). retry_submission only covers FAILED
    submissions, and classify_skipped_file needs a human to pick each
    file's type -- neither helps when a whole link's files were skipped
    because classify itself was broken at the time. The submission is
    FETCHING while this runs, which also blocks manual classification of
    the same files meanwhile."""
    submission = repositories.get_submission(session, submission_id)
    if submission is None:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", "OneDrive submission not found.")
    if submission.status != OneDriveSubmissionStatus.FETCHED.value:
        raise ApiError(409, "SUBMISSION_NOT_FETCHED", "Only a fetched link's skipped files can be reclassified.")
    if not any(_is_reclassifiable(item) for item in submission.skipped_files or []):
        raise ApiError(409, "NOTHING_TO_RECLASSIFY", "This link has no skipped files to reclassify.")

    submission = repositories.mark_fetching(session, submission_id)
    session.commit()

    try:
        logger.info("queued skipped-file reclassify for onedrive submission %s", submission_id)
        executor.submit_reclassify(submission_id)
    except Exception as exc:
        repositories.restore_fetched(session, submission_id)
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to queue the skipped files for reclassification.") from exc
    return submission


def _restore_fetched_safe(session_factory: sessionmaker[Session] | SessionFactory, submission_id: UUID) -> None:
    with session_factory() as session:
        try:
            repositories.restore_fetched(session, submission_id)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to restore onedrive submission %s to FETCHED", submission_id)


def run_skipped_files_reclassify(
    submission_id: UUID,
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    job_executor: JobExecutorProtocol,
    runner: OneDriveFetchRunner | None = None,
) -> None:
    """Background half of reclassify_skipped_files: re-classifies each
    reclassifiable skipped file from its preserved bytes (see
    _record_skipped_file), applies the same neighbour fallback a full fetch
    does, and ingests whatever is now routable exactly like run_onedrive_fetch
    would have. Files still unroutable keep their entry with the new skip
    status; files whose bytes are gone, or whose classify still fails, are
    left untouched (a reviewer can still classify those by hand)."""
    runner = runner or OneDriveFetchRunner(settings)
    try:
        with session_factory() as session:
            submission = repositories.get_submission(session, submission_id)
            if submission is None:
                return
            batch_id = submission.batch_id
            entries = {
                item["filename"]: dict(item) for item in submission.skipped_files or [] if _is_reclassifiable(item)
            }

        classified: list[tuple[Path, Classification]] = []
        page_ocr_by_file: dict[Path, Path] = {}
        for index, filename in enumerate(sorted(entries)):
            storage_key = entries[filename].get("storage_key")
            source_path = settings.storage_root.resolve() / storage_key if storage_key else None
            if (
                storage_key
                and source_path is not None
                and not source_path.is_file()
                and settings.storage_backend == "s3"
            ):
                with contextlib.suppress(Exception):
                    get_storage_service(settings).materialize(storage_key, source_path)
            if source_path is None or not source_path.is_file():
                continue
            page_ocr_by_file[source_path] = _page_ocr_staging_path(settings, submission_id, index)
            try:
                classified.append(
                    (source_path, runner.classify(source_path, page_ocr_output=page_ocr_by_file[source_path]))
                )
            except ClassifyError as exc:
                logger.warning(
                    "reclassify failed for %s (submission %s): %s",
                    filename,
                    submission_id,
                    _summarize_error_text(exc.stderr.strip() or str(exc), _ERROR_MESSAGE_LIMIT),
                )

        classified = _apply_neighbor_fallback(classified)

        ingested = 0
        with session_factory() as session:
            for source_path, classification in classified:
                filename = source_path.name
                entry = repositories.get_skipped_file(session, submission_id, filename)
                if entry is None:
                    continue
                document_type = _route(classification)
                if not isinstance(document_type, DocumentType):
                    if document_type != entry.get("status"):
                        repositories.update_skipped_file(session, submission_id, filename, {"status": document_type})
                        session.commit()
                    continue
                try:
                    _ingest_one_file(
                        session,
                        settings,
                        job_executor,
                        batch_id=batch_id,
                        submission_id=submission_id,
                        source_path=source_path,
                        document_type=document_type,
                        page1_ocr_source=page_ocr_by_file.get(source_path),
                    )
                except UploadValidationError as exc:
                    session.rollback()
                    repositories.update_skipped_file(session, submission_id, filename, {"status": exc.code})
                    session.commit()
                    continue
                except Exception:
                    session.rollback()
                    logger.exception("failed to ingest reclassified %s (submission %s)", filename, submission_id)
                    repositories.update_skipped_file(session, submission_id, filename, {"status": "INGEST_FAILED"})
                    session.commit()
                    continue
                repositories.remove_skipped_file(session, submission_id, filename)
                session.commit()
                ingested += 1
                storage_key = entry.get("storage_key")
                if storage_key and settings.storage_backend == "s3":
                    with contextlib.suppress(Exception):
                        get_storage_service(settings).delete(storage_key)
            repositories.restore_fetched(session, submission_id)
            recompute_batch_status(session, batch_id)
            session.commit()
        shutil.rmtree(_page_ocr_staging_path(settings, submission_id, 0).parent, ignore_errors=True)
        logger.info(
            "reclassified onedrive submission %s: %d of %d skipped file(s) ingested",
            submission_id,
            ingested,
            len(entries),
        )
    except Exception:
        logger.exception("unexpected error reclassifying skipped files for onedrive submission %s", submission_id)
        _restore_fetched_safe(session_factory, submission_id)


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
    skipped_keys = [item["storage_key"] for item in (submission.skipped_files or []) if item.get("storage_key")]

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
            session.execute(select(OCRJob.id, OCRJob.output_relative_path).where(OCRJob.document_id.in_(document_ids)))
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
        page1_ocr_keys = [page1_ocr_relative_path(row.storage_key) for row in documents if row.storage_key]
        for key in (*(row.storage_key for row in documents), *job_output_keys, *skipped_keys, *page1_ocr_keys):
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
    for job_row in job_rows:
        shutil.rmtree(storage_root / "jobs" / str(job_row.id), ignore_errors=True)
    shutil.rmtree(storage_root / "onedrive" / str(submission_id), ignore_errors=True)
