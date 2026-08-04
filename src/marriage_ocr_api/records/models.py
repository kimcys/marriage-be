from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.records.status import RecordStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class OCRRecord(Base):
    __tablename__ = "ocr_records"
    __table_args__ = (UniqueConstraint("job_id", "source_key", name="uq_ocr_records_job_source_key"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("ocr_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
        default=RecordStatus.PENDING_REVIEW.value,
    )
    field_values: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    validation_issues: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    revisions: Mapped[list[RecordRevision]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        order_by="RecordRevision.version.desc()",
    )


class RecordRevision(Base):
    __tablename__ = "record_revisions"
    __table_args__ = (UniqueConstraint("record_id", "version", name="uq_record_revisions_record_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey("ocr_records.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_values: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    new_values: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    record: Mapped[OCRRecord] = relationship(back_populates="revisions")
