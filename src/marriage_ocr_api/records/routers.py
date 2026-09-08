from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.records.api import (
    build_bulk_approve_response,
    build_record_response,
    build_records_page,
    build_revisions_page,
)
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
    RecordResponse,
    RecordReviewRequest,
)
from marriage_ocr_api.records.service import (
    apply_correction,
    approve_record,
    bulk_approve_records,
    delete_record,
    reject_record,
)
from marriage_ocr_api.records.status import RecordStatus

router = APIRouter()


def _not_found(message: str) -> ApiError:
    return ApiError(404, "RECORD_NOT_FOUND", message)


def _conflict(message: str) -> ApiError:
    return ApiError(409, "RECORD_CONFLICT", message)


@router.get("/api/v1/records", response_model=PaginatedRecords, operation_id="list_records")
def list_all_records(
    batch_id: UUID | None = Query(default=None),
    status: RecordStatus | None = Query(default=None),
    q: str | None = Query(default=None, description="Free-text search over the record's extracted field values"),
    source_url: str | None = Query(default=None, description="Filter to records from this OneDrive share link"),
    record_type: str | None = Query(
        default=None, description="Filter to this record type (NIKAH, CERAI, or RUJUK)"
    ),
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
        limit=limit,
        offset=offset,
    )
    total = count_records(
        session, job_id=None, batch_id=batch_id, status=status, q=q, source_url=source_url, record_type=record_type
    )
    filenames = get_document_filenames(session, {item.document_id for item in items if item.document_id})
    return build_records_page(items, limit, offset, total, filenames=filenames)


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
    return build_records_page(items, limit, offset, total, filenames=filenames)


@router.get("/api/v1/records/{record_id}", response_model=RecordResponse, operation_id="get_record")
def get_record(record_id: UUID, session: Session = Depends(get_db_session)) -> RecordResponse:
    try:
        record = get_record_or_raise(session, record_id)
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    return build_record_response(record, original_filename=get_document_filename(session, record.document_id))


@router.delete("/api/v1/records/{record_id}", status_code=204, operation_id="delete_record")
def delete_one_record(record_id: UUID, session: Session = Depends(get_db_session)) -> Response:
    try:
        delete_record(session, record_id)
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
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
) -> RecordResponse:
    try:
        record = apply_correction(
            session,
            record_id,
            expected_version=payload.version,
            field_values=payload.field_values,
            reviewer=None,
            note=payload.note,
        )
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    return build_record_response(record, original_filename=get_document_filename(session, record.document_id))


@router.post(
    "/api/v1/records/{record_id}/approve",
    response_model=RecordResponse,
    operation_id="approve_record",
)
def approve_one_record(
    record_id: UUID,
    payload: RecordReviewRequest,
    session: Session = Depends(get_db_session),
) -> RecordResponse:
    try:
        record = approve_record(
            session,
            record_id,
            expected_version=payload.version,
            reviewer=None,
            note=payload.reason,
        )
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    return build_record_response(record, original_filename=get_document_filename(session, record.document_id))


@router.post(
    "/api/v1/records/{record_id}/reject",
    response_model=RecordResponse,
    operation_id="reject_record",
)
def reject_one_record(
    record_id: UUID,
    payload: RecordReviewRequest,
    session: Session = Depends(get_db_session),
) -> RecordResponse:
    try:
        record = reject_record(
            session,
            record_id,
            expected_version=payload.version,
            reviewer=None,
            reason=payload.reason,
        )
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    return build_record_response(record, original_filename=get_document_filename(session, record.document_id))


@router.post(
    "/api/v1/records/bulk-approve",
    response_model=BulkApproveResponse,
    operation_id="bulk_approve_records",
)
def bulk_approve(
    payload: BulkApproveRequest,
    session: Session = Depends(get_db_session),
) -> BulkApproveResponse:
    try:
        items = bulk_approve_records(session, payload.record_ids, reviewer=payload.reviewer)
        session.commit()
    except RecordNotFoundError as exc:
        raise _not_found("OCR record not found.") from exc
    except RecordConflictError as exc:
        raise _conflict(str(exc)) from exc
    filenames = get_document_filenames(session, {item.document_id for item in items if item.document_id})
    return build_bulk_approve_response(items, filenames=filenames)
