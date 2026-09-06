from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus


class OneDriveSubmissionError(BaseModel):
    code: str
    message: str


class SkippedFile(BaseModel):
    filename: str
    status: str


class OneDriveSubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    url: str
    status: OneDriveSubmissionStatus
    skipped_files: list[SkippedFile] | None
    created_at: datetime
    updated_at: datetime
    fetched_at: datetime | None
    error: OneDriveSubmissionError | None = None
