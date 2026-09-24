from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.repositories import record_activity
from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth.dependencies import require_admin, require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.batches.repositories import (
    get_batch_location,
    get_batch_locations,
    list_distinct_batch_locations,
)
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.records.api import (
    build_bulk_approve_response,
    build_record_response,
    build_records_page,
    build_revisions_page,
)
from marriage_ocr_api.records.models import OCRRecord
from marriage_ocr_api.records.repositories import (
    RecordConflictError,
    RecordNotFoundError,
    count_records,
    get_document_filename,
    get_document_filenames,
    get_record_or_raise,
    list_records,
    list_revisions,
)
from marriage_ocr_api.records.response_models import (
    BulkApproveRequest,
    BulkApproveResponse,
    PaginatedRecordRevisions,
    PaginatedRecords,
    RecordCorrectionRequest,
    RecordLocation,
    RecordLocationsResponse,
    RecordResponse,
    RecordReviewRequest,
)
from marriage_ocr_api.records.service import (
    apply_correction,
    approve_record,
    bulk_approve_records,
    delete_record,
)
from marriage_ocr_api.records.status import RecordStatus

router = APIRouter()


def _not_found(message: str) -> ApiError:
    return ApiError(404, "RECORD_NOT_FOUND", message)


def _conflict(message: str) -> ApiError:
    return ApiError(409, "RECORD_CONFLICT", message)


def _record_label(session: Session, record: OCRRecord) -> str:
    """What an audit entry calls a record: its source file plus the row it
    came from, e.g. "image00012.jpg · page-1-row-3"."""
    filename = get_document_filename(session, record.document_id)
    if filename is None:
        job = session.get(OCRJob, record.job_id)
        filename = job.original_filename if job is not None else None
    return f"{filename} · {record.source_key}" if filename else record.source_key


def _log_record(
    session: Session,
    user: User,
    action: str,
    summary: str,
    record: OCRRecord,
    details: dict[str, Any] | None = None,
) -> None:
    record_activity(
        session,
        user,
        action,
        summary,
        batch_id=record.batch_id,
        target_type="record",
        target_id=record.id,
        target_label=_record_label(session, record),
        details=details,
    )


@router.get("/api/v1/records", response_model=PaginatedRecords, operation_id="list_records")
def list_all_records(
    batch_id: UUID | None = Query(default=None),
    status: RecordStatus | None = Query(default=None),
    q: str | None = Query(default=None, description="Free-text search over the record's extracted field values"),
    source_url: str | None = Query(default=None, description="Filter to records from this OneDrive share link"),
    record_type: str | None = Query(default=None, description="Filter to this record type (NIKAH, CERAI, or RUJUK)"),
    daerah: str | None = Query(default=None, description="Filter to records whose batch is in this daerah"),
    negeri: str | None = Query(default=None, description="Filter to records whose batch is in this negeri"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedRecords:
    items = list_records(
        session,
        job_id=None,
        batch_id=batch_id,
        status=status,
        q=q,
        source_url=source_url,
        record_type=record_type,
        daerah=daerah,
        negeri=negeri,
        limit=limit,
        offset=offset,
    )
    total = count_records(
        session,
        job_id=None,
        batch_id=batch_id,
        status=status,
        q=q,
        source_url=source_url,
        record_type=record_type,
        daerah=daerah,
        negeri=negeri,
    )
    filenames = get_document_filenames(session, {item.document_id for item in items if item.document_id})
    batch_locations = get_batch_locations(session, {item.batch_id for item in items if item.batch_id})
    return build_records_page(items, limit, offset, total, filenames=filenames, batch_locations=batch_locations)


@router.get(
    "/api/v1/jobs/{job_id}/records",
    response_model=PaginatedRecords,
    operation_id="list_job_records",
)
def list_job_records(
    job_id: UUID,
    status: RecordStatus | None = Query(default=None),
    q: str | None = Query(default=None, description="Free-text search over the record's extracted field values"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedRecords:
    items = list_records(session, job_id=job_id, batch_id=None, status=status, q=q, limit=limit, offset=offset)
    total = count_records(session, job_id=job_id, batch_id=None, status=status, q=q)
    filenames = get_document_filenames(session, {item.document_id for item in items if item.document_id})
    batch_locations = get_batch_locations(session, {item.batch_id for item in items if item.batch_id})
    return build_records_page(items, limit, offset, total, filenames=filenames, batch_locations=batch_locations)


@router.get(
    "/api/v1/records/locations",
    response_model=RecordLocationsResponse,
    operation_id="list_record_locations",
)
def list_record_locations(session: Session = Depends(get_db_session)) -> RecordLocationsResponse:
    """Declared before /records/{record_id} so "locations" isn't parsed as
    a record id."""
    return RecordLocationsResponse(
        items=[RecordLocation(daerah=d, negeri=n) for d, n in list_distinct_batch_locations(session)]
    )


@router.get("/api/v1/records/{record_id}", response_model=RecordResponse, operation_id="get_record")
def get_record(record_id: UUID, session: Session = Depends(get_db_session)) -> RecordResponse:
    try:
        record = get_record_or_raise(session, record_id)
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    return build_record_response(
        record,
        original_filename=get_document_filename(session, record.document_id),
        batch_location=get_batch_location(session, record.batch_id),
    )


@router.delete(
    "/api/v1/records/{record_id}", status_code=204, operation_id="delete_record", dependencies=[Depends(require_admin)]
)
def delete_one_record(
    record_id: UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_admin),
) -> Response:
    try:
        record = get_record_or_raise(session, record_id)
        label, batch_id = _record_label(session, record), record.batch_id
        delete_record(session, record_id)
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    record_activity(
        session,
        user,
        "record.deleted",
        f"Deleted record {label}",
        batch_id=batch_id,
        target_type="record",
        target_id=record_id,
        target_label=label,
    )
    session.commit()
    return Response(status_code=204)


@router.get(
    "/api/v1/records/{record_id}/revisions",
    response_model=PaginatedRecordRevisions,
    operation_id="get_record_revisions",
)
def get_record_revisions(
    record_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedRecordRevisions:
    try:
        get_record_or_raise(session, record_id)
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    revisions = list_revisions(session, record_id)
    page = revisions[offset : offset + limit]
    return build_revisions_page(page, limit, offset, len(revisions))


@router.patch(
    "/api/v1/records/{record_id}",
    response_model=RecordResponse,
    operation_id="update_record",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "correction": {
                            "summary": "Correct a record",
                            "value": {
                                "version": 1,
                                "field_values": {"full_name": "Ada Byron"},
                                "note": "corrected surname",
                            },
                        }
                    }
                }
            }
        }
    },
)
def patch_record(
    record_id: UUID,
    payload: RecordCorrectionRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> RecordResponse:
    try:
        before = dict(get_record_or_raise(session, record_id).field_values)
        record = apply_correction(
            session,
            record_id,
            expected_version=payload.version,
            field_values=payload.field_values,
            reviewer=user.code,
            note=payload.note,
        )
        changes = {
            field: [before.get(field), value]
            for field, value in payload.field_values.items()
            if before.get(field) != value
        }
        _log_record(
            session,
            user,
            "record.corrected",
            f"Corrected {len(changes)} field(s): {', '.join(changes) or 'none'}",
            record,
            details={"changes": changes, "version": record.version, "note": payload.note},
        )
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    return build_record_response(
        record,
        original_filename=get_document_filename(session, record.document_id),
        batch_location=get_batch_location(session, record.batch_id),
    )


@router.post(
    "/api/v1/records/{record_id}/approve",
    response_model=RecordResponse,
    operation_id="approve_record",
)
def approve_one_record(
    record_id: UUID,
    payload: RecordReviewRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> RecordResponse:
    try:
        record = approve_record(
            session,
            record_id,
            expected_version=payload.version,
            reviewer=user.code,
            note=payload.reason,
        )
        _log_record(session, user, "record.approved", "Approved record", record, details={"note": payload.reason})
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    return build_record_response(
        record,
        original_filename=get_document_filename(session, record.document_id),
        batch_location=get_batch_location(session, record.batch_id),
    )


@router.post(
    "/api/v1/records/bulk-approve",
    response_model=BulkApproveResponse,
    operation_id="bulk_approve_records",
)
def bulk_approve(
    payload: BulkApproveRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> BulkApproveResponse:
    try:
        # Always the logged-in user -- payload.reviewer is free text any
        # client could set to anyone, so it's no longer trusted for this.
        items = bulk_approve_records(session, payload.record_ids, reviewer=user.code)
        batch_ids = {item.batch_id for item in items if item.batch_id}
        record_activity(
            session,
            user,
            "record.bulk_approved",
            f"Approved {len(items)} record(s) at once",
            batch_id=next(iter(batch_ids)) if len(batch_ids) == 1 else None,
            target_type="record",
            details={"record_ids": [str(item.id) for item in items]},
        )
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    filenames = get_document_filenames(session, {item.document_id for item in items if item.document_id})
    batch_locations = get_batch_locations(session, {item.batch_id for item in items if item.batch_id})
    return build_bulk_approve_response(items, filenames=filenames, batch_locations=batch_locations)
