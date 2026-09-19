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
    # Present only when the original file's bytes were preserved (see
    # onedrive/service.py::_record_skipped_file) -- absent for a skipped
    # file recorded before this field existed, or if preserving it failed.
    # Whether this is set is exactly what tells the frontend a "Classify"
    # action is possible for this file at all.
    classifiable: bool = False


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


class PaginatedOneDriveSubmissions(BaseModel):
    items: list[OneDriveSubmissionResponse]
    limit: int
    offset: int
    total: int
