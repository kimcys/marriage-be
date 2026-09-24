from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.repositories import count_activity, list_activity
from marriage_ocr_api.activity.response_models import ActivityResponse, PaginatedActivity
from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.auth.dependencies import require_admin
from marriage_ocr_api.auth.models import User

# Admin-only: this is how the owner audits what each staff account did.
router = APIRouter(prefix="/api/v1", tags=["activity"], dependencies=[Depends(require_admin)])


@router.get("/activity", response_model=PaginatedActivity, operation_id="list_activity")
def list_all_activity(
    user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, description="e.g. batch.created, record.corrected"),
    batch_id: UUID | None = Query(default=None),
    since: datetime | None = Query(default=None, description="Only entries at or after this time"),
    until: datetime | None = Query(default=None, description="Only entries before this time"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedActivity:
    entries = list_activity(
        session,
        user_id=user_id,
        action=action,
        batch_id=batch_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    user_ids = {entry.user_id for entry in entries if entry.user_id}
    names = (
        {row.id: row.name for row in session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))}
        if user_ids
        else {}
    )
    items = [
        ActivityResponse(
            id=entry.id,
            created_at=entry.created_at,
            user_id=entry.user_id,
            user_code=entry.user_code,
            user_name=names.get(entry.user_id) if entry.user_id else None,
            action=entry.action,
            batch_id=entry.batch_id,
            target_type=entry.target_type,
            target_id=entry.target_id,
            target_label=entry.target_label,
            summary=entry.summary,
            details=entry.details,
        )
        for entry in entries
    ]
    return PaginatedActivity(
        items=items,
        limit=limit,
        offset=offset,
        total=count_activity(session, user_id=user_id, action=action, batch_id=batch_id, since=since, until=until),
    )
