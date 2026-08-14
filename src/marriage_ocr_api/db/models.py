from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class OCRJob(Base):
    __tablename__ = "ocr_jobs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    document_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentType.HANDWRITTEN_REGISTER.value
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    output_relative_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    debug_relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    stdout_log_relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    stderr_log_relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_git_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


from marriage_ocr_api.batches.models import Batch, Document, Export  # noqa: F401,E402
