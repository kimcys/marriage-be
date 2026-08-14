from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.records.status import RecordStatus


class RecordRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    record_id: UUID
    version: int
    previous_values: dict[str, object]
    new_values: dict[str, object]
    reviewer: str | None
    note: str | None
    created_at: datetime


class RecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    source_key: str
    status: RecordStatus
    field_values: dict[str, object]
    confidence: float | None
    validation_issues: list[str]
    reviewed_by: str | None
    reviewed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class PaginatedRecords(BaseModel):
    items: list[RecordResponse]
    limit: int
    offset: int
    total: int


class PaginatedRecordRevisions(BaseModel):
    items: list[RecordRevisionResponse]
    limit: int
    offset: int
    total: int


class RecordCorrectionRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "version": 1,
                    "field_values": {"full_name": "Ada Byron"},
                    "note": "corrected surname",
                }
            ]
        }
    )

    version: int
    field_values: dict[str, object]
    note: str | None = None


class RecordReviewRequest(BaseModel):
    version: int
    reason: str | None = None


class BulkApproveRequest(BaseModel):
    record_ids: list[UUID]
    reviewer: str | None = None


class BulkApproveResponse(BaseModel):
    items: list[RecordResponse]
