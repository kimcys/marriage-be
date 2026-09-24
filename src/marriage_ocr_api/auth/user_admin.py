"""Admin-only user management (the Users page): create accounts, rename
them, switch role, disable/enable, and reset passwords. Every change is
written to the activity log."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.repositories import record_activity
from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth import repositories
from marriage_ocr_api.auth.dependencies import require_admin
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.security import hash_password
from marriage_ocr_api.auth.status import Role

router = APIRouter(prefix="/api/v1/users", tags=["users"], dependencies=[Depends(require_admin)])

MIN_PASSWORD_LENGTH = 8


class UserResponse(BaseModel):
    id: UUID
    code: str
    name: str | None
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    role: Role
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


def _response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        code=user.code,
        name=user.name,
        email=user.email,
        role=Role(user.role),
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _get_or_404(session: Session, user_id: UUID) -> User:
    user = repositories.get_user(session, user_id)
    if user is None:
        raise ApiError(404, "USER_NOT_FOUND", "User not found.")
    return user


def _log(session: Session, admin: User, action: str, summary: str, target: User, details: dict | None = None) -> None:
    record_activity(
        session,
        admin,
        action,
        summary,
        target_type="user",
        target_id=target.id,
        target_label=f"{target.code} · {target.name or target.email}",
        details=details,
    )


@router.get("", response_model=list[UserResponse], operation_id="list_users")
def list_users(session: Session = Depends(get_db_session)) -> list[UserResponse]:
    return [_response(user) for user in session.scalars(select(User).order_by(User.number))]


@router.post("", response_model=UserResponse, status_code=201, operation_id="create_user")
def create_one_user(
    payload: UserCreateRequest,
    session: Session = Depends(get_db_session),
    admin: User = Depends(require_admin),
) -> UserResponse:
    email = payload.email.strip().lower()
    if session.scalar(select(User).where(func.lower(User.email) == email)) is not None:
        raise ApiError(409, "USER_EXISTS", f"An account for {email} already exists.")
    user = repositories.create_user(
        session,
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        name=payload.name.strip(),
    )
    _log(session, admin, "user.created", f"Created {payload.role.value.lower()} account {user.code}", user)
    session.commit()
    return _response(user)


@router.patch("/{user_id}", response_model=UserResponse, operation_id="update_user")
def update_one_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    session: Session = Depends(get_db_session),
    admin: User = Depends(require_admin),
) -> UserResponse:
    user = _get_or_404(session, user_id)
    is_self = user.id == admin.id
    if is_self and payload.role is not None and payload.role.value != user.role:
        raise ApiError(409, "CANNOT_CHANGE_OWN_ROLE", "You can't change your own role.")
    if is_self and payload.is_active is False:
        raise ApiError(409, "CANNOT_DISABLE_SELF", "You can't disable your own account.")

    losing_admin = (
        user.role == Role.ADMIN.value
        and user.is_active
        and ((payload.role is not None and payload.role != Role.ADMIN) or payload.is_active is False)
    )
    if losing_admin:
        active_admins = session.scalar(
            select(func.count()).select_from(User).where(User.role == Role.ADMIN.value, User.is_active.is_(True))
        )
        if (active_admins or 0) <= 1:
            raise ApiError(409, "LAST_ADMIN", "There must always be at least one active admin.")

    before = {"name": user.name, "role": user.role, "is_active": user.is_active}
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.role is not None:
        user.role = payload.role.value
    if payload.is_active is not None:
        user.is_active = payload.is_active
    after = {"name": user.name, "role": user.role, "is_active": user.is_active}
    changes = {field: [before[field], value] for field, value in after.items() if before[field] != value}

    if changes:
        if set(changes) == {"is_active"}:
            action = "user.enabled" if user.is_active else "user.disabled"
            summary = f"{'Enabled' if user.is_active else 'Disabled'} account {user.code}"
        else:
            action = "user.updated"
            summary = f"Edited account {user.code}: " + ", ".join(
                f"{field} {old} → {new}" for field, (old, new) in changes.items()
            )
        _log(session, admin, action, summary, user, details={"changes": changes})
    session.commit()
    return _response(user)


@router.post("/{user_id}/password", status_code=204, operation_id="reset_user_password")
def reset_user_password(
    user_id: UUID,
    payload: PasswordResetRequest,
    session: Session = Depends(get_db_session),
    admin: User = Depends(require_admin),
) -> None:
    user = _get_or_404(session, user_id)
    user.password_hash = hash_password(payload.password)
    _log(session, admin, "user.password_reset", f"Reset the password for {user.code}", user)
    session.commit()
