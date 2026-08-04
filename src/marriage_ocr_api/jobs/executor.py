from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.jobs.paths import build_job_paths
from marriage_ocr_api.jobs.runner import (
    OCRRunRequest,
    SubprocessOCRRunner,
    failure_code_for_run,
    read_sanitized_stderr,
)
from marriage_ocr_api.records.importer import import_records_from_json

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class JobExecutor:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session] | SessionFactory,
        runner: SubprocessOCRRunner,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._runner = runner
        self._pool = ThreadPoolExecutor(max_workers=1)

    def submit(self, job_id: UUID) -> Future[None]:
        return self._pool.submit(self._process_job, job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)

    def _session(self) -> Session:
        return self._session_factory()

    def _mark_failed_safe(self, job_id: UUID, error_code: str, message: str) -> None:
        completed_at = datetime.now(UTC)
        with self._session() as session:
            try:
                repositories.mark_failed(session, job_id, error_code, message, completed_at)
                session.commit()
            except Exception:
                session.rollback()
                logger.exception("failed to mark job %s as failed", job_id)

    def _process_job(self, job_id: UUID) -> None:
        try:
            with self._session() as session:
                started_at = datetime.now(UTC)
                repositories.mark_processing(session, job_id, started_at)
                session.commit()
                job = repositories.get_job(session, job_id)
                if job is None:
                    raise LookupError(f"job {job_id} not found")

            paths = build_job_paths(
                self.settings.storage_root,
                job.id,
                Path(job.stored_filename).suffix,
            )
            request = OCRRunRequest(
                input_path=paths.input_source_path,
                output_path=paths.output_result_path,
                debug_path=paths.debug_dir,
                stdout_log_path=paths.stdout_log_path,
                stderr_log_path=paths.stderr_log_path,
            )
            result = self._runner.run(request)
            failure_code = failure_code_for_run(result, request.output_path)
            if failure_code is None:
                completed_at = datetime.now(UTC)
                with self._session() as session:
                    repositories.mark_completed(
                        session,
                        job_id,
                        paths.output_relative_path,
                        completed_at,
                    )
                    import_records_from_json(session, job_id, paths.output_result_path.with_suffix(".json"))
                    session.commit()
                return

            stderr_excerpt = read_sanitized_stderr(
                request.stderr_log_path,
                self.settings.ocr_stderr_api_limit,
            )
            if failure_code == "OCR_PROCESS_TIMEOUT":
                error_message = "OCR processing timed out."
            elif failure_code == "OCR_OUTPUT_MISSING":
                error_message = "OCR processing completed without producing an output file."
            else:
                error_message = stderr_excerpt or "OCR processing failed."
            self._mark_failed_safe(job_id, failure_code, error_message)
        except Exception:
            logger.exception("unexpected OCR processing error for job %s", job_id)
            self._mark_failed_safe(
                job_id,
                "INTERNAL_PROCESSING_ERROR",
                "The OCR job encountered an internal processing error.",
            )
