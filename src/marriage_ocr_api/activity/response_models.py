from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ActivityResponse(BaseModel):
    id: UUID
    created_at: datetime
    user_id: UUID | None
    # The code at the time of the action (survives the user being deleted),
    # and the user's current name, for the hover label.
    user_code: str | None
    user_name: str | None
    action: str
    batch_id: UUID | None
    target_type: str | None
    target_id: str | None
    target_label: str | None
    summary: str
    details: dict[str, Any] | None


class PaginatedActivity(BaseModel):
    items: list[ActivityResponse]
    limit: int
    offset: int
    total: int
