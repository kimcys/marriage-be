from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, get_onedrive_executor
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import get_batch
from marriage_ocr_api.onedrive import repositories
from marriage_ocr_api.onedrive.response_models import OneDriveSubmissionResponse
from marriage_ocr_api.onedrive.schemas import OneDriveLinkCreateRequest
from marriage_ocr_api.onedrive.service import (
    OneDriveExecutorProtocol,
    build_submission_response,
    get_or_create_submission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/batches", tags=["onedrive"])


def _batch_not_found(batch_id: UUID) -> ApiError:
    return ApiError(404, "BATCH_NOT_FOUND", f"Batch {batch_id} not found.")


@router.post(
    "/{batch_id}/onedrive-links",
    response_model=OneDriveSubmissionResponse,
    operation_id="submit_onedrive_link",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "link": {
                            "summary": "Submit a OneDrive share link",
                            "value": {"url": "https://1drv.ms/f/s!AbCdEfGhIjKlMnOp"},
                        }
                    }
                }
            }
        }
    },
)
def submit_onedrive_link(
    batch_id: UUID,
    payload: OneDriveLinkCreateRequest,
    session: Session = Depends(get_db_session),
    executor: OneDriveExecutorProtocol = Depends(get_onedrive_executor),
) -> JSONResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    submission, created = get_or_create_submission(session, batch_id=batch_id, url=payload.url)
    response = build_submission_response(submission)

    if not created:
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    try:
        executor.submit(submission.id)
    except Exception as exc:
        logger.exception("failed to submit onedrive fetch %s for batch %s", submission.id, batch_id)
        repositories.mark_failed(
            session,
            submission.id,
            error_code="INTERNAL_PROCESSING_ERROR",
            error_message="The OneDrive link could not be submitted for background processing.",
        )
        session.commit()
        raise ApiError(500, "INTERNAL_ERROR", "Failed to submit the OneDrive link for background processing.") from exc
    return JSONResponse(status_code=202, content=response.model_dump(mode="json"))
