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
    daerah: str | None = None
    negeri: str | None = None


class BatchRenameRequest(BaseModel):
    """PATCH /batches/{batch_id}'s payload -- a full replace of name/daerah/
    negeri together (not a sparse merge), matching the frontend's combined
    "edit batch" form. daerah/negeri default to None so an old caller that
    only ever sent {"name": ...} still validates."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Batch 1 (renamed)"}]})

    name: str
    daerah: str | None = None
    negeri: str | None = None


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    daerah: str | None
    negeri: str | None
    status: BatchStatus
    # The user who added the batch: their id, display code (e.g. MOCR001)
    # and name. All null if the creator was never recorded or has since
    # been deleted.
    created_by: UUID | None
    created_by_code: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class PaginatedBatches(BaseModel):
    items: list[BatchResponse]
    limit: int
    offset: int
    total: int


class BatchStatsResponse(BaseModel):
    """Dashboard stat-card counts: total batches plus a breakdown by every
    BatchStatus value. `needs_attention` folds FAILED and CANCELLED together
    -- both mean a reviewer has to act on that batch, whereas the other
    statuses (DRAFT/QUEUED/PROCESSING/REVIEW_REQUIRED/COMPLETED) don't."""

    total: int
    processing: int
    completed: int
    needs_attention: int
    by_status: dict[BatchStatus, int]
