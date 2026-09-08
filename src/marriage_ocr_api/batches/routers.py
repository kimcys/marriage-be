from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import count_batches, create_batch, get_batch, list_batches
from marriage_ocr_api.batches.response_models import BatchCreateRequest, BatchResponse, PaginatedBatches
from marriage_ocr_api.batches.service import delete_batch
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
) -> BatchResponse:
    batch = create_batch(session, name=payload.name, description=payload.description, created_by=None)
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


@router.get("/{batch_id}", response_model=BatchResponse, operation_id="get_batch")
def get_one_batch(batch_id: UUID, session: Session = Depends(get_db_session)) -> BatchResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)
    return BatchResponse.model_validate(batch)


@router.delete("/{batch_id}", status_code=204, operation_id="delete_batch")
def delete_one_batch(
    batch_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> Response:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)
    delete_batch(session, settings, batch_id)
    return Response(status_code=204)
