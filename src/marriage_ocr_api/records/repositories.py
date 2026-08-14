from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marriage_ocr_api.records.models import OCRRecord, RecordRevision
from marriage_ocr_api.records.status import RecordStatus


class RecordNotFoundError(RuntimeError):
    pass


class RecordConflictError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _get_record_by_key(
    session: Session,
    *,
    job_id: UUID,
    source_key: str,
    source_page: int | None = None,
    source_record_index: int = 0,
) -> OCRRecord | None:
    stmt: Select[tuple[OCRRecord]] = select(OCRRecord).where(
        OCRRecord.job_id == job_id,
        OCRRecord.source_key == source_key,
        OCRRecord.source_page == source_page,
        OCRRecord.source_record_index == source_record_index,
    )
    return session.scalar(stmt)


def create_record(
    session: Session,
    *,
    job_id: UUID,
    batch_id: UUID | None = None,
    document_id: UUID | None = None,
    source_key: str,
    source_page: int | None = None,
    source_record_index: int = 0,
    field_values: dict[str, object],
    normalized_data: dict[str, object] | None = None,
    corrected_data: dict[str, object] | None = None,
    confidence: float | None,
    validation_issues: list[str],
    status: RecordStatus = RecordStatus.PENDING_REVIEW,
    review_status: str = "PENDING",
    reviewed_by: str | None = None,
    reviewed_at: datetime | None = None,
    version: int = 1,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> OCRRecord:
    existing = _get_record_by_key(
        session,
        job_id=job_id,
        source_key=source_key,
        source_page=source_page,
        source_record_index=source_record_index,
    )
    if existing is not None:
        raise RecordConflictError(f"record {job_id}:{source_key} already exists")

    now = utcnow()
    record = OCRRecord(
        job_id=job_id,
        batch_id=batch_id,
        document_id=document_id,
        source_key=source_key,
        source_page=source_page,
        source_record_index=source_record_index,
        field_values=field_values,
        normalized_data=normalized_data or field_values,
        corrected_data=corrected_data or {},
        confidence=confidence,
        validation_issues=validation_issues,
        status=status.value,
        review_status=review_status,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        version=version,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError as exc:
        raise RecordConflictError(f"record {job_id}:{source_key} already exists") from exc
    return record


def create_record_if_missing(
    session: Session,
    *,
    job_id: UUID,
    batch_id: UUID | None = None,
    document_id: UUID | None = None,
    payload: dict[str, object],
) -> bool:
    source_key = str(payload["source_key"])
    source_page = payload.get("source_page")
    source_record_index = payload.get("source_record_index")
    source_page = source_page if isinstance(source_page, int) else None
    source_record_index = source_record_index if isinstance(source_record_index, int) else 0
    if (
        _get_record_by_key(
            session,
            job_id=job_id,
            source_key=source_key,
            source_page=source_page,
            source_record_index=source_record_index,
        )
        is not None
    ):
        return False
    field_values = payload.get("field_values", {})
    if not isinstance(field_values, dict):
        field_values = {}
    validation_issues = payload.get("validation_issues", [])
    if not isinstance(validation_issues, list):
        validation_issues = []
    normalized_data = payload.get("normalized_data")
    corrected_data = payload.get("corrected_data")
    create_record(
        session,
        job_id=job_id,
        batch_id=batch_id,
        document_id=document_id,
        source_key=source_key,
        source_page=source_page,
        source_record_index=source_record_index,
        field_values=cast(dict[str, object], field_values),
        normalized_data=cast(dict[str, object], normalized_data) if isinstance(normalized_data, dict) else None,
        corrected_data=cast(dict[str, object], corrected_data) if isinstance(corrected_data, dict) else None,
        confidence=cast(float | None, payload.get("confidence")),
        validation_issues=cast(list[str], validation_issues),
    )
    return True


def get_record(session: Session, record_id: UUID) -> OCRRecord | None:
    return session.get(OCRRecord, record_id)


def get_record_or_raise(session: Session, record_id: UUID) -> OCRRecord:
    record = get_record(session, record_id)
    if record is None:
        raise RecordNotFoundError(f"record {record_id} does not exist")
    return record


def list_records(
    session: Session,
    *,
    job_id: UUID | None,
    batch_id: UUID | None,
    status: RecordStatus | None,
    limit: int,
    offset: int,
) -> list[OCRRecord]:
    stmt: Select[tuple[OCRRecord]] = select(OCRRecord)
    if job_id is not None:
        stmt = stmt.where(OCRRecord.job_id == job_id)
    if batch_id is not None:
        stmt = stmt.where(OCRRecord.batch_id == batch_id)
    if status is not None:
        stmt = stmt.where(OCRRecord.status == status.value)
    stmt = stmt.order_by(OCRRecord.created_at.desc(), OCRRecord.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_records(
    session: Session,
    *,
    job_id: UUID | None,
    batch_id: UUID | None,
    status: RecordStatus | None,
) -> int:
    stmt = select(func.count()).select_from(OCRRecord)
    if job_id is not None:
        stmt = stmt.where(OCRRecord.job_id == job_id)
    if batch_id is not None:
        stmt = stmt.where(OCRRecord.batch_id == batch_id)
    if status is not None:
        stmt = stmt.where(OCRRecord.status == status.value)
    return int(session.scalar(stmt) or 0)


def list_revisions(session: Session, record_id: UUID) -> list[RecordRevision]:
    stmt: Select[tuple[RecordRevision]] = select(RecordRevision).where(RecordRevision.record_id == record_id)
    stmt = stmt.order_by(RecordRevision.version.desc(), RecordRevision.created_at.desc())
    return list(session.scalars(stmt))


def append_revision(
    session: Session,
    *,
    record_id: UUID,
    version: int,
    previous_values: dict[str, object],
    new_values: dict[str, object],
    reviewer: str | None,
    note: str | None,
) -> RecordRevision:
    revision = RecordRevision(
        record_id=record_id,
        version=version,
        previous_values=previous_values,
        new_values=new_values,
        reviewer=reviewer,
        note=note,
    )
    session.add(revision)
    session.flush()
    return revision
