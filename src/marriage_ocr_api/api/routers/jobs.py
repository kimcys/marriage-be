from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, get_job_executor, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.schemas import JobResponse, PaginatedJobs
from marriage_ocr_api.jobs.service import (
    build_job_response,
    create_and_submit_job,
    get_job_or_raise,
    retry_job,
    sanitize_stem,
)
from marriage_ocr_api.jobs.service import (
    list_jobs as list_jobs_service,
)
from marriage_ocr_api.jobs.status import JobStatus

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _resolve_storage_path(storage_root: Path, relative_path: str) -> Path:
    resolved_root = storage_root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ApiError(500, "INTERNAL_ERROR", "Invalid stored path.")
    return resolved


@router.post("", response_model=JobResponse, status_code=202, operation_id="create_job_upload")
def create_job(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    document_type: DocumentType = Form(DocumentType.HANDWRITTEN_REGISTER),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> JobResponse:
    job = create_and_submit_job(file, session, get_job_executor(request), settings, document_type=document_type)
    job_response = build_job_response(job)
    response.headers["Location"] = job_response.links.self
    return job_response


@router.get("", response_model=PaginatedJobs, operation_id="list_jobs")
def list_jobs(
    status: JobStatus | None = Query(default=None),
    document_id: UUID | None = Query(default=None),
    batch_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedJobs:
    return list_jobs_service(session, status, limit, offset, document_id=document_id, batch_id=batch_id)


@router.get("/{job_id}", response_model=JobResponse, operation_id="get_job")
def get_job(job_id: UUID, session: Session = Depends(get_db_session)) -> JobResponse:
    return build_job_response(get_job_or_raise(job_id, session))


@router.post("/{job_id}/retry", response_model=JobResponse, operation_id="retry_job")
def retry_one_job(
    request: Request,
    job_id: UUID,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    job = retry_job(job_id, session, get_job_executor(request))
    return build_job_response(job)


@router.get("/{job_id}/download", operation_id="download_job")
def download_job(
    job_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> FileResponse:
    job = get_job_or_raise(job_id, session)
    if job.status != JobStatus.COMPLETED.value:
        raise ApiError(409, "JOB_NOT_COMPLETED", "The OCR job has not completed yet.")
    if not job.output_relative_path:
        raise ApiError(410, "OUTPUT_FILE_MISSING", "The expected output file is missing.")
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
