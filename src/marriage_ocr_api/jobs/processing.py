from __future__ import annotations

import logging
from datetime import UTC, datetime
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

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


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
