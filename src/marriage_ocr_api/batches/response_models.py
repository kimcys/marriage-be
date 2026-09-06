from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.batches.status import BatchStatus


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Batch 1",
                    "description": "Example batch for the frontend contract.",
                }
            ]
        }
    )

    name: str
    description: str | None = None


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    status: BatchStatus
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class PaginatedBatches(BaseModel):
    items: list[BatchResponse]
    limit: int
    offset: int
    total: int
