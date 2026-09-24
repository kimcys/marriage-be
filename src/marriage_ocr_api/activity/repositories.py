from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.models import ActivityLog
from marriage_ocr_api.auth.models import User


def record_activity(
    session: Session,
    user: User,
    action: str,
    summary: str,
    *,
    batch_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    target_label: str | None = None,
    details: dict[str, Any] | None = None,
) -> ActivityLog:
    """Adds one audit entry to `session` -- the caller commits it, ideally
    in the same transaction as the change it describes. Called only after
    the action has succeeded, so the log never claims something that didn't
    happen."""
    entry = ActivityLog(
        user_id=user.id,
        user_code=user.code,
        action=action,
        summary=summary[:1000],
        batch_id=batch_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        target_label=target_label[:1000] if target_label else None,
        details=details,
    )
    session.add(entry)
    return entry


def _apply_filters[T: Select[Any]](
    stmt: T,
    *,
    user_id: UUID | None,
    action: str | None,
    batch_id: UUID | None,
    since: datetime | None,
    until: datetime | None,
) -> T:
    if user_id is not None:
        stmt = stmt.where(ActivityLog.user_id == user_id)
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    if batch_id is not None:
        stmt = stmt.where(ActivityLog.batch_id == batch_id)
    if since is not None:
        stmt = stmt.where(ActivityLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(ActivityLog.created_at < until)
    return stmt


def list_activity(
    session: Session,
    *,
    user_id: UUID | None = None,
    action: str | None = None,
    batch_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int,
    offset: int,
) -> list[ActivityLog]:
    stmt = _apply_filters(
        select(ActivityLog), user_id=user_id, action=action, batch_id=batch_id, since=since, until=until
    )
    stmt = stmt.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_activity(
    session: Session,
    *,
    user_id: UUID | None = None,
    action: str | None = None,
    batch_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    stmt = _apply_filters(
        select(func.count()).select_from(ActivityLog),
        user_id=user_id,
        action=action,
        batch_id=batch_id,
        since=since,
        until=until,
    )
    return int(session.scalar(stmt) or 0)
