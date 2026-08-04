from __future__ import annotations

from marriage_ocr_api.records.models import OCRRecord, RecordRevision
from marriage_ocr_api.records.response_models import (
    BulkApproveResponse,
    PaginatedRecordRevisions,
    PaginatedRecords,
    RecordResponse,
    RecordRevisionResponse,
)


def build_record_response(record: OCRRecord) -> RecordResponse:
    return RecordResponse.model_validate(record)


def build_revision_response(revision: RecordRevision) -> RecordRevisionResponse:
    return RecordRevisionResponse.model_validate(revision)


def build_records_page(items: list[OCRRecord], limit: int, offset: int, total: int) -> PaginatedRecords:
    return PaginatedRecords(
        items=[build_record_response(item) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )


def build_revisions_page(
    items: list[RecordRevision],
    limit: int,
    offset: int,
    total: int,
) -> PaginatedRecordRevisions:
    return PaginatedRecordRevisions(
        items=[build_revision_response(item) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )


def build_bulk_approve_response(items: list[OCRRecord]) -> BulkApproveResponse:
    return BulkApproveResponse(items=[build_record_response(item) for item in items])
