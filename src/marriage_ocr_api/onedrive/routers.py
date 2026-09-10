from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, get_onedrive_executor, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import get_batch
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.onedrive import repositories
from marriage_ocr_api.onedrive.response_models import (
    OneDriveSubmissionResponse,
    PaginatedOneDriveSubmissions,
)
from marriage_ocr_api.onedrive.schemas import OneDriveLinkCreateRequest
from marriage_ocr_api.onedrive.service import (
    OneDriveExecutorProtocol,
    build_submission_response,
    delete_submission,
    get_or_create_submission,
    retry_submission,
)
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus

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

    if not created:
        # Resubmitting a link that previously failed is the only way a
        # plain paste-and-submit can recover it -- otherwise a user has to
        # know the separate "Retry" action exists at all. A duplicate of
        # anything still in-flight or already fetched is left alone, same
        # as before (see get_or_create_submission's docstring).
        if submission.status == OneDriveSubmissionStatus.FAILED.value:
            submission = retry_submission(session, submission.id, executor)
            response = build_submission_response(submission)
            return JSONResponse(status_code=202, content=response.model_dump(mode="json"))
        response = build_submission_response(submission)
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    response = build_submission_response(submission)
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


@router.get(
    "/{batch_id}/onedrive-links",
    response_model=PaginatedOneDriveSubmissions,
    operation_id="list_onedrive_links",
)
def list_onedrive_links(
    batch_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedOneDriveSubmissions:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    items = repositories.list_submissions(session, batch_id=batch_id, limit=limit, offset=offset)
    total = repositories.count_submissions(session, batch_id=batch_id)
    return PaginatedOneDriveSubmissions(
        items=[build_submission_response(item) for item in items],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "/{batch_id}/onedrive-links/{submission_id}/retry",
    response_model=OneDriveSubmissionResponse,
    operation_id="retry_onedrive_link",
)
def retry_onedrive_link(
    batch_id: UUID,
    submission_id: UUID,
    session: Session = Depends(get_db_session),
    executor: OneDriveExecutorProtocol = Depends(get_onedrive_executor),
) -> OneDriveSubmissionResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    submission = repositories.get_submission(session, submission_id)
    if submission is None or submission.batch_id != batch_id:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", f"OneDrive submission {submission_id} not found in this batch.")

    submission = retry_submission(session, submission_id, executor)
    return build_submission_response(submission)


@router.delete(
    "/{batch_id}/onedrive-links/{submission_id}",
    status_code=204,
    operation_id="delete_onedrive_link",
)
def delete_onedrive_link(
    batch_id: UUID,
    submission_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> Response:
    """Deletes one OneDrive link and everything it caused to be ingested --
    its documents, their OCR jobs, and any records those jobs produced.
    The batch itself, and anything ingested by its other links, are left
    alone."""
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    submission = repositories.get_submission(session, submission_id)
    if submission is None or submission.batch_id != batch_id:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", f"OneDrive submission {submission_id} not found in this batch.")

    delete_submission(session, settings, submission_id)
    return Response(status_code=204)
