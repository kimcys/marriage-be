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
    # Whether "Classify" can be offered right now. Preserved bytes (see
    # onedrive/service.py::_record_skipped_file) are used directly; without
    # them the file is re-downloaded from the submission's link instead, so
    # this is only false while that re-download is already in progress.
    classifiable: bool = False
    # Set while (IN_PROGRESS) or after (FAILED) a background re-download of
    # this one file from the submission's OneDrive link -- what "Classify"
    # falls back to when the preserved bytes are gone (see
    # onedrive/service.py::run_skipped_file_refetch).
    refetch_status: str | None = None
    refetch_error: str | None = None


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
