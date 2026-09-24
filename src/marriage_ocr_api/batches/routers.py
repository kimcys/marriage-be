from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.repositories import record_activity
from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth.dependencies import require_admin, require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.batches.repositories import (
    count_batches,
    count_batches_by_status,
    create_batch,
    get_batch,
    get_document,
    list_batches,
    rename_batch,
)
from marriage_ocr_api.batches.response_models import (
    BatchCreateRequest,
    BatchRenameRequest,
    BatchResponse,
    BatchStatsResponse,
    PaginatedBatches,
)
from marriage_ocr_api.batches.service import build_document_download_response, cancel_batch_processing, delete_batch
from marriage_ocr_api.batches.status import BatchStatus
from marriage_ocr_api.core.config import Settings

router = APIRouter(prefix="/api/v1/batches", tags=["batches"])


def _batch_not_found(batch_id: UUID) -> ApiError:
    return ApiError(404, "BATCH_NOT_FOUND", f"Batch {batch_id} not found.")


@router.post(
    "",
    response_model=BatchResponse,
    status_code=201,
    operation_id="create_batch",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "batch": {
                            "summary": "Create a batch",
                            "value": {
                                "name": "Batch 1",
                                "description": "Example batch for the frontend contract.",
                            },
                        }
                    }
                }
            }
        }
    },
)
def create_one_batch(
    payload: BatchCreateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> BatchResponse:
    batch = create_batch(
        session,
        name=payload.name,
        description=payload.description,
        created_by=user.id,
        daerah=payload.daerah,
        negeri=payload.negeri,
    )
    record_activity(
        session,
        user,
        "batch.created",
        f'Added batch "{batch.name}"',
        batch_id=batch.id,
        target_type="batch",
        target_id=batch.id,
        target_label=batch.name,
        details={"daerah": batch.daerah, "negeri": batch.negeri},
    )
    session.commit()
    return BatchResponse.model_validate(batch)


@router.get("", response_model=PaginatedBatches, operation_id="list_batches")
def list_all_batches(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedBatches:
    items = [BatchResponse.model_validate(batch) for batch in list_batches(session, limit, offset)]
    return PaginatedBatches(items=items, limit=limit, offset=offset, total=count_batches(session))


@router.get("/stats", response_model=BatchStatsResponse, operation_id="get_batch_stats")
def get_batch_stats(session: Session = Depends(get_db_session)) -> BatchStatsResponse:
    """Dashboard stat-card counts, computed live from the current batches
    table rather than cached -- cheap at this row count (one batch per
    intake, not per document/job/record) and always exactly in sync with
    the table below it on the same page."""
    by_status = count_batches_by_status(session)
    needs_attention = by_status[BatchStatus.FAILED] + by_status[BatchStatus.CANCELLED]
    return BatchStatsResponse(
        total=sum(by_status.values()),
        processing=by_status[BatchStatus.PROCESSING],
        completed=by_status[BatchStatus.COMPLETED],
        needs_attention=needs_attention,
        by_status=by_status,
    )


@router.get("/{batch_id}", response_model=BatchResponse, operation_id="get_batch")
def get_one_batch(batch_id: UUID, session: Session = Depends(get_db_session)) -> BatchResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)
    return BatchResponse.model_validate(batch)


@router.patch("/{batch_id}", response_model=BatchResponse, operation_id="rename_batch")
def rename_one_batch(
    batch_id: UUID,
    payload: BatchRenameRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> BatchResponse:
    existing = get_batch(session, batch_id)
    before = (
        {"name": existing.name, "daerah": existing.daerah, "negeri": existing.negeri} if existing is not None else {}
    )
    batch = rename_batch(session, batch_id, payload.name, daerah=payload.daerah, negeri=payload.negeri)
    if batch is None:
        raise _batch_not_found(batch_id)
    after = {"name": batch.name, "daerah": batch.daerah, "negeri": batch.negeri}
    changes = {field: [before.get(field), value] for field, value in after.items() if before.get(field) != value}
    if changes:
        record_activity(
            session,
            user,
            "batch.updated",
            f'Edited batch "{batch.name}": '
            + ", ".join(f"{field} {old or '—'} → {new or '—'}" for field, (old, new) in changes.items()),
            batch_id=batch.id,
            target_type="batch",
            target_id=batch.id,
            target_label=batch.name,
            details={"changes": changes},
        )
    session.commit()
    return BatchResponse.model_validate(batch)


@router.delete("/{batch_id}", status_code=204, operation_id="delete_batch", dependencies=[Depends(require_admin)])
def delete_one_batch(
    batch_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
    user: User = Depends(require_admin),
) -> Response:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)
    name = batch.name
    delete_batch(session, settings, batch_id)
    record_activity(
        session,
        user,
        "batch.deleted",
        f'Deleted batch "{name}" and everything in it',
        batch_id=batch_id,
        target_type="batch",
        target_id=batch_id,
        target_label=name,
    )
    session.commit()
    return Response(status_code=204)


@router.post("/{batch_id}/cancel", response_model=BatchResponse, operation_id="cancel_batch_processing")
def cancel_one_batch_processing(
    batch_id: UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_user),
) -> BatchResponse:
    """Stops the batch's in-flight OCR processing -- every PENDING job is
    skipped and every PROCESSING job's subprocess is killed. Safe to call
    when nothing is running (no-op, 200) rather than erroring."""
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)
    batch = cancel_batch_processing(session, batch_id)
    record_activity(
        session,
        user,
        "batch.processing_stopped",
        f'Stopped OCR processing for batch "{batch.name}"',
        batch_id=batch.id,
        target_type="batch",
        target_id=batch.id,
        target_label=batch.name,
    )
    session.commit()
    return BatchResponse.model_validate(batch)


@router.get("/{batch_id}/documents/{document_id}/download", response_model=None, operation_id="download_document")
def download_one_document(
    batch_id: UUID,
    document_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> FileResponse | RedirectResponse:
    document = get_document(session, document_id)
    if document is None or document.batch_id != batch_id:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", f"Document {document_id} not found in this batch.")
    return build_document_download_response(document, settings)
