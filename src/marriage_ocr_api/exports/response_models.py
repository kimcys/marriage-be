from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.batches.schemas import ExportFormat
from marriage_ocr_api.batches.status import ExportStatus


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "batch_id": "123e4567-e89b-12d3-a456-426614174000",
                    "format": "XLSX",
                    "include_unreviewed": False,
                }
            ]
        }
    )

    batch_id: UUID
    format: ExportFormat
    include_unreviewed: bool = False
    created_by: UUID | None = None


class ExportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    format: str
    status: ExportStatus
    storage_key: str | None
    record_count: int | None
    created_by: UUID | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class PaginatedExports(BaseModel):
    items: list[ExportResponse]
    limit: int
    offset: int
    total: int
