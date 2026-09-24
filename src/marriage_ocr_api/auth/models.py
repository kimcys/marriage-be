from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.db.base import Base

USER_CODE_PREFIX = "MOCR"


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Short, human-friendly id (1, 2, 3, ... in account-creation order) for
    # display -- e.g. a batch's "Added by". `id` stays the real primary key
    # (JWT `sub`, foreign keys); this is assigned by create_user.
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # The person's display name, shown when hovering over their code.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.REVIEWER.value)
    # Disabled instead of deleted: a disabled account can't log in (and its
    # existing token stops working), but stays attributed on everything it
    # did -- deleting it would blank out "Added by" and the activity log.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def code(self) -> str:
        """Display id shown wherever a user is attributed, e.g. MOCR001."""
        return f"{USER_CODE_PREFIX}{self.number:03d}"
