from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from marriage_ocr_api.activity.repositories import record_activity
from marriage_ocr_api.api.dependencies import (
    get_db_session,
    get_job_executor,
    get_onedrive_executor,
    settings_dependency,
)
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth.dependencies import require_admin, require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.batches.repositories import get_batch
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.service import JobExecutorProtocol
from marriage_ocr_api.onedrive import repositories
from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.response_models import (
    OneDriveSubmissionResponse,
    PaginatedOneDriveSubmissions,
)
from marriage_ocr_api.onedrive.schemas import OneDriveLinkCreateRequest, SkippedFileClassifyRequest
from marriage_ocr_api.onedrive.service import (
    OneDriveExecutorProtocol,
    build_submission_response,
    classify_skipped_file,
    delete_submission,
    get_or_create_submission,
    retry_submission,
)
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/batches", tags=["onedrive"])


def _log_link(session: Session, user: User, action: str, summary: str, submission: OneDriveSubmission) -> None:
    record_activity(
        session,
        user,
        action,
        summary,
        batch_id=submission.batch_id,
        target_type="onedrive_link",
        target_id=submission.id,
        target_label=submission.url,
    )


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
    user: User = Depends(require_user),
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
            _log_link(session, user, "onedrive.link_retried", "Retried OneDrive link", submission)
            session.commit()
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
    _log_link(session, user, "onedrive.link_submitted", f'Submitted OneDrive link to batch "{batch.name}"', submission)
    session.commit()
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
    user: User = Depends(require_user),
) -> OneDriveSubmissionResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    submission = repositories.get_submission(session, submission_id)
    if submission is None or submission.batch_id != batch_id:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", f"OneDrive submission {submission_id} not found in this batch.")

    submission = retry_submission(session, submission_id, executor)
    _log_link(session, user, "onedrive.link_retried", "Retried OneDrive link", submission)
    session.commit()
    return build_submission_response(submission)


@router.post(
    "/{batch_id}/onedrive-links/{submission_id}/skipped-files/classify",
    response_model=OneDriveSubmissionResponse,
    operation_id="classify_skipped_file",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "classify": {
                            "summary": "Manually classify a file the auto-classifier couldn't route",
                            "value": {"filename": "image00001.jpg", "document_type": "HANDWRITTEN_REGISTER"},
                        }
                    }
                }
            }
        }
    },
)
def classify_skipped_onedrive_file(
    batch_id: UUID,
    submission_id: UUID,
    payload: SkippedFileClassifyRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
    job_executor: JobExecutorProtocol = Depends(get_job_executor),
    onedrive_executor: OneDriveExecutorProtocol = Depends(get_onedrive_executor),
    user: User = Depends(require_user),
) -> OneDriveSubmissionResponse:
    """Lets a reviewer pick the document type for a file that came back
    NEEDS_MANUAL_CLASSIFICATION (or any other non-ROUTABLE skip reason) --
    the auto-classifier's keyword-matching heuristic couldn't tell what it
    was, but a human looking at it usually can. Routes it into the same
    Document/Job pipeline every auto-classified file already goes through.
    If the file's bytes are no longer on hand, just that file is queued to
    be re-downloaded from the link and ingested in the background instead.
    """
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    submission = repositories.get_submission(session, submission_id)
    if submission is None or submission.batch_id != batch_id:
        raise ApiError(404, "SUBMISSION_NOT_FOUND", f"OneDrive submission {submission_id} not found in this batch.")

    result = classify_skipped_file(
        session,
        settings,
        job_executor,
        submission_id,
        filename=payload.filename,
        document_type=payload.document_type,
        onedrive_executor=onedrive_executor,
    )
    refetching = any(
        item.filename == payload.filename and item.refetch_status is not None for item in result.skipped_files or []
    )
    record_activity(
        session,
        user,
        "skipped_file.classified",
        f"Classified {payload.filename} as {payload.document_type.value}"
        + (" (re-downloading from OneDrive)" if refetching else ""),
        batch_id=batch_id,
        target_type="onedrive_link",
        target_id=submission_id,
        target_label=payload.filename,
        details={
            "filename": payload.filename,
            "document_type": payload.document_type.value,
            "re_downloaded": refetching,
        },
    )
    session.commit()
    return result


@router.delete(
    "/{batch_id}/onedrive-links/{submission_id}",
    status_code=204,
    operation_id="delete_onedrive_link",
    dependencies=[Depends(require_admin)],
)
def delete_onedrive_link(
    batch_id: UUID,
    submission_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
    user: User = Depends(require_admin),
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

    url = submission.url
    delete_submission(session, settings, submission_id)
    record_activity(
        session,
        user,
        "onedrive.link_deleted",
        f'Deleted OneDrive link from batch "{batch.name}" and everything it ingested',
        batch_id=batch_id,
        target_type="onedrive_link",
        target_id=submission_id,
        target_label=url,
    )
    session.commit()
    return Response(status_code=204)
