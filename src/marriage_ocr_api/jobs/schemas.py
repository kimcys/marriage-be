from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.jobs.status import JobStatus


class JobError(BaseModel):
    code: str
    message: str


class JobLinks(BaseModel):
    self: str
    download: str | None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: JobStatus
    original_filename: str
    content_type: str
    file_size_bytes: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: JobError | None
    links: JobLinks


class PaginatedJobs(BaseModel):
    items: list[JobResponse]
    limit: int
    offset: int
    total: int
