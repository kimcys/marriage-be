from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class OneDriveSubmission(Base):
    __tablename__ = "onedrive_submissions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # UNIQUE is the actual dedup guarantee (see onedrive/service.py) -- a
    # second concurrent POST of the same link that races past the
    # look-up-first check still can't create a duplicate row.
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OneDriveSubmissionStatus.PENDING.value, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # List of {"filename": ..., "status": ...} for files the classify step
    # couldn't route -- kept separate from Document/OCRJob rows since these
    # were never routable in the first place (see onedrive/service.py).
    skipped_files: Mapped[list[dict[str, str]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
