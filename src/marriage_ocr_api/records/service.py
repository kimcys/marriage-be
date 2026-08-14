from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from marriage_ocr_api.records.models import OCRRecord
from marriage_ocr_api.records.repositories import (
    RecordConflictError,
    RecordNotFoundError,
    append_revision,
    get_record_or_raise,
)
from marriage_ocr_api.records.status import RecordStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def apply_correction(
    session: Session,
    record_id: UUID,
    *,
    expected_version: int,
    field_values: dict[str, object],
    reviewer: str | None,
    note: str | None,
) -> OCRRecord:
    record = get_record_or_raise(session, record_id)
    if record.version != expected_version:
        raise RecordConflictError("The record has been modified by another review action.")

    next_version = record.version + 1
    append_revision(
        session,
        record_id=record.id,
        version=next_version,
        previous_values=dict(record.field_values),
        new_values=dict(field_values),
        reviewer=reviewer,
        note=note,
    )
    merged_field_values = {**record.field_values, **field_values}
    record.field_values = merged_field_values
    record.corrected_data = {**record.corrected_data, **field_values}
    record.status = RecordStatus.PENDING_REVIEW.value
    record.review_status = RecordStatus.PENDING_REVIEW.value
    record.reviewed_by = reviewer
    record.reviewed_at = utcnow()
    record.version = next_version
    session.flush()
    return record


def _set_review_status(
    session: Session,
    record_id: UUID,
    *,
    expected_version: int,
    status: RecordStatus,
    reviewer: str | None,
    note: str | None,
) -> OCRRecord:
    record = get_record_or_raise(session, record_id)
    if record.version != expected_version:
        raise RecordConflictError("The record has been modified by another review action.")
    if record.status == status.value:
        return record

    next_version = record.version + 1
    append_revision(
        session,
        record_id=record.id,
        version=next_version,
        previous_values=dict(record.field_values),
        new_values=dict(record.field_values),
        reviewer=reviewer,
        note=note,
    )
    record.status = status.value
    record.review_status = status.value
    record.reviewed_by = reviewer
    record.reviewed_at = utcnow()
    record.version = next_version
    session.flush()
    return record


def approve_record(
    session: Session,
    record_id: UUID,
    *,
    expected_version: int,
    reviewer: str | None,
    note: str | None = None,
) -> OCRRecord:
    return _set_review_status(
        session,
        record_id,
        expected_version=expected_version,
        status=RecordStatus.APPROVED,
        reviewer=reviewer,
        note=note,
    )


def reject_record(
    session: Session,
    record_id: UUID,
    *,
    expected_version: int,
    reviewer: str | None,
    reason: str | None,
) -> OCRRecord:
    return _set_review_status(
        session,
        record_id,
        expected_version=expected_version,
        status=RecordStatus.REJECTED,
        reviewer=reviewer,
        note=reason,
    )


def bulk_approve_records(
    session: Session,
    record_ids: list[UUID],
    *,
    reviewer: str | None,
) -> list[OCRRecord]:
    approved: list[OCRRecord] = []
    for record_id in record_ids:
        record = get_record_or_raise(session, record_id)
        approved.append(
            approve_record(
                session,
                record_id,
                expected_version=record.version,
                reviewer=reviewer,
            )
        )
    return approved


__all__ = [
    "RecordConflictError",
    "RecordNotFoundError",
    "approve_record",
    "apply_correction",
    "bulk_approve_records",
    "reject_record",
]
