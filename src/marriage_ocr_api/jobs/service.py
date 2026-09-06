from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import recompute_batch_status, recompute_document_status
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.schemas import JobError, JobLinks, JobResponse, PaginatedJobs
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.storage.factory import get_storage_service

logger = logging.getLogger(__name__)


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
        batch_id=job.batch_id,
        document_id=job.document_id,
        status=JobStatus(job.status),
        document_type=DocumentType(job.document_type),
        page_number=job.page_number,
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


def get_job_or_raise(job_id: UUID, session: Session) -> OCRJob:
    job = repositories.get_job(session, job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "OCR job not found.")
    return job


def _resolve_storage_path(storage_root: Path, relative_path: str) -> Path:
    resolved_root = storage_root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ApiError(500, "INTERNAL_ERROR", "Invalid stored path.")
    return resolved


def build_job_download_response(job: OCRJob, settings: Settings) -> FileResponse | RedirectResponse:
    """Same STORAGE_BACKEND=s3 awareness as exports.service.build_export_
    download_response: the worker that produced this job's output may not
    be the same node as the API instance handling this request, so local
    disk can't be assumed -- redirect to a presigned Spaces/S3 URL instead
    when one is available."""
    if job.status != JobStatus.COMPLETED.value:
        raise ApiError(409, "JOB_NOT_COMPLETED", "The OCR job has not completed yet.")
    if not job.output_relative_path:
        raise ApiError(410, "OUTPUT_FILE_MISSING", "The expected output file is missing.")

    storage = get_storage_service(settings)
    signed_url = storage.signed_download_url(job.output_relative_path, expires_seconds=300)
    if signed_url is not None:
        return RedirectResponse(signed_url)

    output_path = _resolve_storage_path(settings.storage_root, job.output_relative_path)
    if not output_path.exists() or not output_path.is_file() or output_path.stat().st_size == 0:
        raise ApiError(410, "OUTPUT_FILE_MISSING", "The expected output file is missing.")
    filename = f"{sanitize_stem(job.original_filename)}-result.xlsx"
    return FileResponse(
        path=output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


def retry_job(job_id: UUID, session: Session, executor: JobExecutorProtocol) -> OCRJob:
    job = get_job_or_raise(job_id, session)
    if job.status != JobStatus.FAILED.value:
        raise ApiError(409, "JOB_NOT_FAILED", "Only a failed job can be retried.")

    job = repositories.mark_pending_for_retry(session, job_id)
    session.commit()

    try:
        logger.info("resubmitting OCR job %s for retry", job_id)
        executor.submit(job_id)
    except Exception as exc:
        completed_at = _utcnow()
        repositories.mark_failed(
            session,
            job_id,
            "INTERNAL_PROCESSING_ERROR",
            "The OCR job could not be resubmitted for background processing.",
            completed_at,
        )
        if job.document_id is not None:
            recompute_document_status(session, job.document_id)
        if job.batch_id is not None:
            recompute_batch_status(session, job.batch_id)
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to resubmit OCR job.") from exc
    return job


def list_jobs(
    session: Session,
    status: JobStatus | None,
    limit: int,
    offset: int,
    *,
    document_id: UUID | None = None,
    batch_id: UUID | None = None,
) -> PaginatedJobs:
    items = [
        build_job_response(job)
        for job in repositories.list_jobs(session, status, limit, offset, document_id=document_id, batch_id=batch_id)
    ]
    return PaginatedJobs(
        items=items,
        limit=limit,
        offset=offset,
        total=repositories.count_jobs(session, status, document_id=document_id, batch_id=batch_id),
    )


def recover_interrupted_jobs(session: Session) -> int:
    failed_jobs = repositories.fail_interrupted_jobs(session, _utcnow())
    _recompute_status_for_jobs(session, failed_jobs)
    return len(failed_jobs)


def _recompute_status_for_jobs(session: Session, jobs: list[OCRJob]) -> None:
    """Recompute each affected job's document/batch status exactly once.

    recompute_document_status/recompute_batch_status treat any
    pending/processing job as blocking, so a job that just got marked
    FAILED by recovery (startup or periodic) must trigger a recompute --
    otherwise its document/batch stays stuck at PROCESSING forever even
    though the job itself is now terminal.
    """
    seen_document_ids: set[UUID] = set()
    seen_batch_ids: set[UUID] = set()
    for job in jobs:
        if job.document_id is not None and job.document_id not in seen_document_ids:
            seen_document_ids.add(job.document_id)
            recompute_document_status(session, job.document_id)
        if job.batch_id is not None and job.batch_id not in seen_batch_ids:
            seen_batch_ids.add(job.batch_id)
            recompute_batch_status(session, job.batch_id)


def recover_stale_jobs(session: Session, stale_after_seconds: float) -> int:
    """Periodic (not startup-only) recovery for jobs abandoned by a crashed
    or killed worker mid-run. See repositories.fail_stale_processing_jobs.
    """
    failed_jobs = repositories.fail_stale_processing_jobs(session, _utcnow(), stale_after_seconds)
    _recompute_status_for_jobs(session, failed_jobs)
    return len(failed_jobs)
