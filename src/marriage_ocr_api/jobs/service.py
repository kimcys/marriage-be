from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.paths import build_job_paths
from marriage_ocr_api.jobs.schemas import JobError, JobLinks, JobResponse, PaginatedJobs
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.storage.local import UploadValidationError, save_upload


class JobExecutorProtocol(Protocol):
    def submit(self, job_id: UUID) -> object: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def sanitize_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("._-")
    return sanitized or "job"


def build_job_response(job: OCRJob) -> JobResponse:
    error = None
    if job.error_code or job.error_message:
        error = JobError(code=job.error_code or "INTERNAL_ERROR", message=job.error_message or "")
    download = f"/api/v1/jobs/{job.id}/download" if job.status == JobStatus.COMPLETED.value else None
    return JobResponse(
        id=job.id,
        status=JobStatus(job.status),
        original_filename=job.original_filename,
        content_type=job.content_type,
        file_size_bytes=job.file_size_bytes,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=error,
        links=JobLinks(self=f"/api/v1/jobs/{job.id}", download=download),
    )


def create_and_submit_job(
    upload: UploadFile,
    session: Session,
    executor: JobExecutorProtocol,
    settings: Settings,
) -> OCRJob:
    job_id = uuid4()
    paths = build_job_paths(settings.storage_root, job_id)
    try:
        stored = save_upload(upload, paths, settings)
        job = repositories.create_job(
            session,
            id=job_id,
            status=JobStatus.PENDING,
            original_filename=upload.filename or "",
            stored_filename=stored.stored_filename,
            content_type=stored.content_type,
            file_size_bytes=stored.file_size_bytes,
            input_relative_path=stored.input_relative_path,
            debug_relative_path=paths.debug_relative_path,
            stdout_log_relative_path=paths.stdout_log_relative_path,
            stderr_log_relative_path=paths.stderr_log_relative_path,
            ocr_git_ref=settings.marriage_ocr_git_ref,
        )
        session.commit()
    except UploadValidationError:
        raise
    except Exception as exc:
        session.rollback()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to create job.") from exc

    try:
        executor.submit(job_id)
    except Exception as exc:
        repositories.mark_failed(
            session,
            job_id,
            "INTERNAL_PROCESSING_ERROR",
            "The OCR job could not be submitted for background processing.",
            _utcnow(),
        )
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to submit OCR job.") from exc
    return job


def get_job_or_raise(job_id: UUID, session: Session) -> OCRJob:
    job = repositories.get_job(session, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "OCR job not found.")
    return job


def list_jobs(
    session: Session,
    status: JobStatus | None,
    limit: int,
    offset: int,
) -> PaginatedJobs:
    items = [build_job_response(job) for job in repositories.list_jobs(session, status, limit, offset)]
    return PaginatedJobs(
        items=items,
        limit=limit,
        offset=offset,
        total=repositories.count_jobs(session, status),
    )


def recover_interrupted_jobs(session: Session) -> int:
    return repositories.fail_interrupted_jobs(session, _utcnow())
