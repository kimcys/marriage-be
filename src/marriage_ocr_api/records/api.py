from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from marriage_ocr_api.records.models import OCRRecord, RecordRevision
from marriage_ocr_api.records.response_models import (
    BulkApproveResponse,
    PaginatedRecordRevisions,
    PaginatedRecords,
    RecordResponse,
    RecordRevisionResponse,
)


def build_record_response(
    record: OCRRecord,
    *,
    original_filename: str | None = None,
    batch_location: tuple[str | None, str | None] = (None, None),
) -> RecordResponse:
    response = RecordResponse.model_validate(record)
    response.original_filename = original_filename
    response.batch_daerah, response.batch_negeri = batch_location
    return response


def build_revision_response(revision: RecordRevision) -> RecordRevisionResponse:
    return RecordRevisionResponse.model_validate(revision)


def build_records_page(
    items: list[OCRRecord],
    limit: int,
    offset: int,
    total: int,
    *,
    filenames: Mapping[UUID, str] | None = None,
    batch_locations: Mapping[UUID, tuple[str | None, str | None]] | None = None,
) -> PaginatedRecords:
    filenames = filenames or {}
    batch_locations = batch_locations or {}
    return PaginatedRecords(
        items=[
            build_record_response(
                item,
                original_filename=filenames.get(item.document_id) if item.document_id else None,
                batch_location=batch_locations.get(item.batch_id, (None, None)) if item.batch_id else (None, None),
            )
            for item in items
        ],
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


def build_bulk_approve_response(
    items: list[OCRRecord],
    *,
    filenames: Mapping[UUID, str] | None = None,
    batch_locations: Mapping[UUID, tuple[str | None, str | None]] | None = None,
) -> BulkApproveResponse:
    filenames = filenames or {}
    batch_locations = batch_locations or {}
    return BulkApproveResponse(
        items=[
            build_record_response(
                item,
                original_filename=filenames.get(item.document_id) if item.document_id else None,
                batch_location=batch_locations.get(item.batch_id, (None, None)) if item.batch_id else (None, None),
            )
            for item in items
        ]
    )
