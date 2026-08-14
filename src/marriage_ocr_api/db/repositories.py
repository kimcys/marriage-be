from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.db.models import OCRJob, utcnow
from marriage_ocr_api.jobs.status import JobStatus


class JobTransitionError(RuntimeError):
    pass


def create_job(
    session: Session,
    *,
    id: UUID,
    batch_id: UUID | None = None,
    document_id: UUID | None = None,
    status: JobStatus,
    document_type: DocumentType = DocumentType.HANDWRITTEN_REGISTER,
    page_number: int | None = None,
    original_filename: str,
    stored_filename: str,
    content_type: str,
    file_size_bytes: int,
    input_relative_path: str,
    debug_relative_path: str,
    stdout_log_relative_path: str,
    stderr_log_relative_path: str,
    ocr_git_ref: str,
    output_relative_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> OCRJob:
    now = utcnow()
    job = OCRJob(
        id=id,
        batch_id=batch_id,
        document_id=document_id,
        status=status.value,
        document_type=document_type.value,
        page_number=page_number,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        input_relative_path=input_relative_path,
        output_relative_path=output_relative_path,
        debug_relative_path=debug_relative_path,
        stdout_log_relative_path=stdout_log_relative_path,
        stderr_log_relative_path=stderr_log_relative_path,
        ocr_git_ref=ocr_git_ref,
        error_code=error_code,
        error_message=error_message,
        started_at=started_at,
        completed_at=completed_at,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: UUID) -> OCRJob | None:
    return session.get(OCRJob, job_id)


def list_jobs(
    session: Session,
    status: JobStatus | None,
    limit: int,
    offset: int,
    *,
    document_id: UUID | None = None,
    batch_id: UUID | None = None,
) -> list[OCRJob]:
    stmt: Select[tuple[OCRJob]] = select(OCRJob)
    if status is not None:
        stmt = stmt.where(OCRJob.status == status.value)
    if document_id is not None:
        stmt = stmt.where(OCRJob.document_id == document_id)
    if batch_id is not None:
        stmt = stmt.where(OCRJob.batch_id == batch_id)
    stmt = stmt.order_by(OCRJob.page_number.asc().nulls_last(), OCRJob.created_at.desc(), OCRJob.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_jobs(
    session: Session,
    status: JobStatus | None,
    *,
    document_id: UUID | None = None,
    batch_id: UUID | None = None,
) -> int:
    stmt = select(func.count()).select_from(OCRJob)
    if status is not None:
        stmt = stmt.where(OCRJob.status == status.value)
    if document_id is not None:
        stmt = stmt.where(OCRJob.document_id == document_id)
    if batch_id is not None:
        stmt = stmt.where(OCRJob.batch_id == batch_id)
    return int(session.scalar(stmt) or 0)


def _require_job(session: Session, job_id: UUID) -> OCRJob:
    job = session.get(OCRJob, job_id)
    if job is None:
        raise JobTransitionError(f"job {job_id} does not exist")
    return job


def mark_processing(session: Session, job_id: UUID, started_at: datetime) -> OCRJob:
    job = _require_job(session, job_id)
    if job.status != JobStatus.PENDING.value:
        raise JobTransitionError(f"job {job_id} is not pending")
    job.status = JobStatus.PROCESSING.value
    job.started_at = started_at
    job.updated_at = started_at
    session.flush()
    return job


def mark_completed(
    session: Session,
    job_id: UUID,
    output_relative_path: str,
    completed_at: datetime,
) -> OCRJob:
    job = _require_job(session, job_id)
    if job.status != JobStatus.PROCESSING.value:
        raise JobTransitionError(f"job {job_id} is not processing")
    job.status = JobStatus.COMPLETED.value
    job.output_relative_path = output_relative_path
    job.completed_at = completed_at
    job.updated_at = completed_at
    session.flush()
    return job


def mark_failed(
    session: Session,
    job_id: UUID,
    error_code: str,
    error_message: str,
    completed_at: datetime,
) -> OCRJob:
    job = _require_job(session, job_id)
    if job.status not in {JobStatus.PENDING.value, JobStatus.PROCESSING.value}:
        raise JobTransitionError(f"job {job_id} cannot fail from status {job.status}")
    job.status = JobStatus.FAILED.value
    job.error_code = error_code
    job.error_message = error_message
    job.completed_at = completed_at
    job.updated_at = completed_at
    session.flush()
    return job


def mark_pending_for_retry(session: Session, job_id: UUID) -> OCRJob:
    job = _require_job(session, job_id)
    if job.status != JobStatus.FAILED.value:
        raise JobTransitionError(f"job {job_id} is not failed")
    job.status = JobStatus.PENDING.value
    job.error_code = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.updated_at = utcnow()
    session.flush()
    return job


def fail_interrupted_jobs(session: Session, completed_at: datetime) -> int:
    jobs = list(session.scalars(select(OCRJob).where(OCRJob.status == JobStatus.PROCESSING.value)))
    for job in jobs:
        job.status = JobStatus.FAILED.value
        job.error_code = "PROCESS_INTERRUPTED"
        job.error_message = "OCR processing was interrupted by an application restart."
        job.completed_at = completed_at
        job.updated_at = completed_at
    session.flush()
    return len(jobs)
