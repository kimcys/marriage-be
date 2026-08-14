from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from starlette.responses import Response

from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import count_exports, get_batch, get_export, list_exports
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.exports.response_models import ExportCreateRequest, ExportResponse, PaginatedExports
from marriage_ocr_api.exports.service import build_export_download_response, create_export_artifact

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


def _export_not_found(export_id: UUID) -> ApiError:
    return ApiError(404, "EXPORT_NOT_FOUND", f"Export {export_id} not found.")


def _batch_not_found(batch_id: UUID) -> ApiError:
    return ApiError(404, "BATCH_NOT_FOUND", f"Batch {batch_id} not found.")


@router.post(
    "",
    response_model=ExportResponse,
    status_code=202,
    operation_id="create_export",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "export": {
                            "summary": "Create an export",
                            "value": {
                                "batch_id": "123e4567-e89b-12d3-a456-426614174000",
                                "format": "XLSX",
                                "include_unreviewed": False,
                            },
                        }
                    }
                }
            }
        }
    },
)
def create_one_export(
    payload: ExportCreateRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> ExportResponse:
    batch = get_batch(session, payload.batch_id)
    if batch is None:
        raise _batch_not_found(payload.batch_id)
    export = create_export_artifact(
        session,
        settings,
        batch_id=payload.batch_id,
        format=payload.format,
        include_unreviewed=payload.include_unreviewed,
        created_by=payload.created_by,
    )
    return ExportResponse.model_validate(export)


@router.get("", response_model=PaginatedExports, operation_id="list_exports")
def list_all_exports(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PaginatedExports:
    items = [ExportResponse.model_validate(export) for export in list_exports(session, limit, offset)]
    return PaginatedExports(items=items, limit=limit, offset=offset, total=count_exports(session))


@router.get("/{export_id}", response_model=ExportResponse, operation_id="get_export")
def get_one_export(export_id: UUID, session: Session = Depends(get_db_session)) -> ExportResponse:
    export = get_export(session, export_id)
    if export is None:
        raise _export_not_found(export_id)
    return ExportResponse.model_validate(export)


@router.get("/{export_id}/download", response_model=None, operation_id="download_export")
def download_export(
    export_id: UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> Response:
    export = get_export(session, export_id)
    if export is None:
        raise _export_not_found(export_id)
    try:
        return build_export_download_response(export, settings)
    except FileNotFoundError as exc:
        raise ApiError(410, "EXPORT_FILE_MISSING", "The expected export file is missing.") from exc
