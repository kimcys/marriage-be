from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.batches.repositories import recompute_batch_status, recompute_document_status
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.jobs.runner import (
    OCRRunRequest,
    SubprocessOCRRunner,
    failure_code_for_run,
    read_sanitized_stderr,
)
from marriage_ocr_api.records.importer import import_records_from_csv, import_records_from_xlsx
from marriage_ocr_api.storage.factory import get_storage_service

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _ensure_input_materialized(settings: Settings, input_path: Path, input_relative_path: str) -> None:
    """Pull the job's input file down from S3/Spaces if it isn't already on
    this machine's local disk. Needed once the API/OneDrive-fetch task and
    the Celery worker that actually runs OCR can be on different Droplets --
    a worker's local disk otherwise has no way to see a file another node
    wrote. A no-op for STORAGE_BACKEND=local (single-filesystem deployments)
    and for a worker that happens to already have the file locally."""
    if settings.storage_backend != "s3" or input_path.exists():
        return
    get_storage_service(settings).materialize(input_relative_path, input_path)


def _mark_failed_safe(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    job_id: UUID,
    error_code: str,
    message: str,
) -> None:
    completed_at = datetime.now(UTC)
    with session_factory() as session:
        try:
            repositories.mark_failed(session, job_id, error_code, message, completed_at)
            job = repositories.get_job(session, job_id)
            if job is not None and job.document_id is not None:
                recompute_document_status(session, job.document_id)
            if job is not None and job.batch_id is not None:
                recompute_batch_status(session, job.batch_id)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("failed to mark job %s as failed", job_id)


def process_ocr_job(
    job_id: UUID,
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    runner: SubprocessOCRRunner,
) -> None:
    """Run one OCR job end-to-end: mark processing, invoke the CLI, import records.

    Shared by the in-process thread-pool executor and the Celery worker task so
    the two dispatch mechanisms can't drift on how a job is actually processed.
    """
    try:
        logger.info("starting OCR job %s", job_id)
        with session_factory() as session:
            started_at = datetime.now(UTC)
            repositories.mark_processing(session, job_id, started_at)
            session.commit()
            job = repositories.get_job(session, job_id)
            if job is None:
                raise LookupError(f"job {job_id} not found")
            if job.document_id is not None:
                recompute_document_status(session, job.document_id)
            if job.batch_id is not None:
                recompute_batch_status(session, job.batch_id)
            session.commit()

        document_type = DocumentType(job.document_type)
        is_typed = document_type == DocumentType.TYPED_BORANG_4B

        storage_root = settings.storage_root.resolve()
        input_path = storage_root / job.input_relative_path
        debug_path = storage_root / job.debug_relative_path
        stdout_log_path = storage_root / job.stdout_log_relative_path
        stderr_log_path = storage_root / job.stderr_log_relative_path
        output_extension = ".csv" if is_typed else ".xlsx"
        output_path = debug_path.parent / "output" / f"result{output_extension}"
        _ensure_input_materialized(settings, input_path, job.input_relative_path)
        request = OCRRunRequest(
            input_path=input_path,
            output_path=output_path,
            debug_path=debug_path,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            document_type=document_type,
        )
        result = runner.run(request)
        failure_code = failure_code_for_run(result, request.output_path)
        if failure_code is None:
            completed_at = datetime.now(UTC)
            output_relative_path = output_path.relative_to(storage_root).as_posix()
            if settings.storage_backend == "s3":
                # Push the result up so any API instance can serve
                # /jobs/{id}/download -- the worker that produced it may not
                # be the same node as the one handling that request.
                get_storage_service(settings).put_file(output_path, output_relative_path)
            with session_factory() as session:
                repositories.mark_completed(
                    session,
                    job_id,
                    output_relative_path,
                    completed_at,
                )
                if is_typed:
                    import_records_from_csv(
                        session,
                        job_id,
                        output_path,
                        batch_id=job.batch_id,
                        document_id=job.document_id,
                        override_source_page=job.page_number,
                    )
                else:
                    import_records_from_xlsx(
                        session,
                        job_id,
                        output_path,
                        batch_id=job.batch_id,
                        document_id=job.document_id,
                        override_source_page=job.page_number,
                    )
                if job.document_id is not None:
                    recompute_document_status(session, job.document_id)
                if job.batch_id is not None:
                    recompute_batch_status(session, job.batch_id)
                session.commit()
            logger.info("completed OCR job %s", job_id)
            return

        stderr_excerpt = read_sanitized_stderr(
            request.stderr_log_path,
            settings.ocr_stderr_api_limit,
        )
        if failure_code == "OCR_PROCESS_TIMEOUT":
            error_message = "OCR processing timed out."
        elif failure_code == "OCR_OUTPUT_MISSING":
            error_message = "OCR processing completed without producing an output file."
        else:
            error_message = stderr_excerpt or "OCR processing failed."
        _mark_failed_safe(settings, session_factory, job_id, failure_code, error_message)
        logger.info("failed OCR job %s with %s", job_id, failure_code)
    except Exception:
        logger.exception("unexpected OCR processing error for job %s", job_id)
        _mark_failed_safe(
            settings,
            session_factory,
            job_id,
            "INTERNAL_PROCESSING_ERROR",
            "The OCR job encountered an internal processing error.",
        )
