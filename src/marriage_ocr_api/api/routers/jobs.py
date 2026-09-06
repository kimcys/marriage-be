from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, get_job_executor, settings_dependency
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.schemas import JobResponse, PaginatedJobs
from marriage_ocr_api.jobs.service import build_job_download_response, build_job_response, get_job_or_raise, retry_job
from marriage_ocr_api.jobs.service import (
    list_jobs as list_jobs_service,
)
from marriage_ocr_api.jobs.status import JobStatus

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


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


@router.get("/{job_id}/download", response_model=None, operation_id="download_job")
def download_job(
    job_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> FileResponse | RedirectResponse:
    job = get_job_or_raise(job_id, session)
    return build_job_download_response(job, settings)
