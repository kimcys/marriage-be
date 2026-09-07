from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, String, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.onedrive.models import OneDriveSubmission
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


def _initial_status_for(missing_fields: list[str]) -> RecordStatus:
    """A record with nothing missing needs no reviewer action at all --
    APPROVED from the moment it's created. Only a record with at least one
    flagged field starts PENDING_REVIEW, so the review queue only ever
    surfaces records that actually need attention (see
    records/service.py::apply_correction for the matching re-check when a
    missing field is filled in)."""
    return RecordStatus.PENDING_REVIEW if missing_fields else RecordStatus.APPROVED


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
    missing_fields: list[str] | None = None,
    status: RecordStatus | None = None,
    review_status: str | None = None,
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

    missing_fields = missing_fields or []
    resolved_status = status if status is not None else _initial_status_for(missing_fields)
    resolved_review_status = review_status if review_status is not None else resolved_status.value

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
        missing_fields=missing_fields,
        status=resolved_status.value,
        review_status=resolved_review_status,
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
    missing_fields = payload.get("missing_fields", [])
    if not isinstance(missing_fields, list):
        missing_fields = []
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
        missing_fields=cast(list[str], missing_fields),
    )
    return True


def get_record(session: Session, record_id: UUID) -> OCRRecord | None:
    return session.get(OCRRecord, record_id)


def get_record_or_raise(session: Session, record_id: UUID) -> OCRRecord:
    record = get_record(session, record_id)
    if record is None:
        raise RecordNotFoundError(f"record {record_id} does not exist")
    return record


def _apply_record_filters[T: Select[Any]](
    stmt: T,
    *,
    job_id: UUID | None,
    batch_id: UUID | None,
    status: RecordStatus | None,
    q: str | None,
    source_url: str | None,
) -> T:
    if job_id is not None:
        stmt = stmt.where(OCRRecord.job_id == job_id)
    if batch_id is not None:
        stmt = stmt.where(OCRRecord.batch_id == batch_id)
    if status is not None:
        stmt = stmt.where(OCRRecord.status == status.value)
    if q:
        # Free-text search over the extracted field values (e.g. full_name,
        # ic_number) without needing to know which field to match on --
        # different record types (nikah/cerai/rujuk) have different field
        # schemas, so a per-field filter can't cover all of them uniformly.
        # ilike() compiles to a portable case-insensitive match on both
        # Postgres and SQLite (used in tests).
        stmt = stmt.where(sql_cast(OCRRecord.field_values, String).ilike(f"%{q}%"))
    if source_url:
        # A record's source_url is resolved through the document it came
        # from, which is only set for documents ingested via a OneDrive
        # link -- a directly-uploaded document has no onedrive_submission_id
        # and so never matches this filter.
        stmt = (
            stmt.join(Document, OCRRecord.document_id == Document.id)
            .join(OneDriveSubmission, Document.onedrive_submission_id == OneDriveSubmission.id)
            .where(OneDriveSubmission.url == source_url)
        )
    return stmt


def list_records(
    session: Session,
    *,
    job_id: UUID | None,
    batch_id: UUID | None,
    status: RecordStatus | None,
    q: str | None = None,
    source_url: str | None = None,
    limit: int,
    offset: int,
) -> list[OCRRecord]:
    stmt: Select[tuple[OCRRecord]] = select(OCRRecord)
    stmt = _apply_record_filters(stmt, job_id=job_id, batch_id=batch_id, status=status, q=q, source_url=source_url)
    stmt = stmt.order_by(OCRRecord.created_at.desc(), OCRRecord.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def count_records(
    session: Session,
    *,
    job_id: UUID | None,
    batch_id: UUID | None,
    status: RecordStatus | None,
    q: str | None = None,
    source_url: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(OCRRecord)
    stmt = _apply_record_filters(stmt, job_id=job_id, batch_id=batch_id, status=status, q=q, source_url=source_url)
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
