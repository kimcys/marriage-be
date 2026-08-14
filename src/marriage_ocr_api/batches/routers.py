from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, get_job_executor, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.batches.pdf_splitter import pdf_page_count, split_pdf_into_pages
from marriage_ocr_api.batches.repositories import (
    count_batches,
    create_batch,
    create_document,
    get_batch,
    list_batches,
    recompute_batch_status,
    recompute_document_status,
)
from marriage_ocr_api.batches.response_models import (
    BatchCreateRequest,
    BatchResponse,
    DocumentResponse,
    PaginatedBatches,
)
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.repositories import create_job, mark_failed
from marriage_ocr_api.jobs.paths import JobPaths, build_job_paths
from marriage_ocr_api.jobs.service import JobExecutorProtocol
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.storage.factory import get_storage_service
from marriage_ocr_api.storage.local import UploadValidationError, save_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/batches", tags=["batches"])


def _document_paths(storage_root: Path, batch_id: UUID, document_id: UUID, extension: str = ".pdf") -> JobPaths:
    resolved_root = storage_root.resolve()
    document_root = (resolved_root / "batches" / str(batch_id) / "documents" / str(document_id)).resolve()
    if not document_root.is_relative_to(resolved_root):
        raise ValueError("generated document paths must remain inside STORAGE_ROOT")
    input_dir = document_root / "input"
    output_dir = document_root / "output"
    debug_dir = document_root / "debug"
    logs_dir = document_root / "logs"
    normalized = extension.lower()
    return JobPaths(
        storage_root=resolved_root,
        job_root=document_root,
        input_dir=input_dir,
        output_dir=output_dir,
        debug_dir=debug_dir,
        logs_dir=logs_dir,
        input_source_path=input_dir / f"source{normalized}",
        input_part_path=input_dir / f"source{normalized}.part",
        output_result_path=output_dir / "result.xlsx",
        stdout_log_path=logs_dir / "stdout.log",
        stderr_log_path=logs_dir / "stderr.log",
    )


def _batch_not_found(batch_id: UUID) -> ApiError:
    return ApiError(404, "BATCH_NOT_FOUND", f"Batch {batch_id} not found.")


def _split_page_count(document_type: DocumentType, media_type: str, input_path: Path) -> int | None:
    """Return the page count if this document should be split into per-page jobs.

    Only handwritten-register PDFs with more than one page are split -- typed
    Borang 4B forms span two physical pages per logical record, so splitting
    those would sever a record in half.
    """
    if document_type != DocumentType.HANDWRITTEN_REGISTER or media_type != "application/pdf":
        return None
    page_count = pdf_page_count(input_path)
    return page_count if page_count > 1 else None


def _create_document_page_jobs(
    session: Session,
    settings: Settings,
    *,
    batch_id: UUID,
    document: Document,
    input_path: Path,
    page_count: int,
    document_type: DocumentType,
) -> list[UUID]:
    staging_dir = input_path.parent / "pages"
    page_paths = split_pdf_into_pages(input_path, staging_dir)
    job_ids: list[UUID] = []
    try:
        for page_number, page_source_path in enumerate(page_paths, start=1):
            page_job_id = uuid4()
            job_paths = build_job_paths(settings.storage_root, page_job_id, ".pdf")
            job_paths.input_dir.mkdir(parents=True, exist_ok=True)
            # Keep the "page-N.pdf" name (rather than job_paths' generic "source.pdf")
            # so the input file stays traceable to its original page on disk.
            input_path = job_paths.input_dir / page_source_path.name
            shutil.move(str(page_source_path), input_path)
            input_relative_path = input_path.relative_to(job_paths.storage_root).as_posix()
            create_job(
                session,
                id=page_job_id,
                batch_id=batch_id,
                document_id=document.id,
                status=JobStatus.PENDING,
                document_type=document_type,
                page_number=page_number,
                original_filename=document.original_filename,
                stored_filename=input_path.name,
                content_type="application/pdf",
                file_size_bytes=input_path.stat().st_size,
                input_relative_path=input_relative_path,
                debug_relative_path=job_paths.debug_relative_path,
                stdout_log_relative_path=job_paths.stdout_log_relative_path,
                stderr_log_relative_path=job_paths.stderr_log_relative_path,
                ocr_git_ref=settings.marriage_ocr_git_ref,
            )
            job_ids.append(page_job_id)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return job_ids


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


@router.post(
    "/{batch_id}/documents",
    response_model=DocumentResponse,
    status_code=202,
    operation_id="upload_batch_document",
)
def upload_document(
    batch_id: UUID,
    file: UploadFile = File(...),
    document_type: DocumentType = Form(DocumentType.HANDWRITTEN_REGISTER),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
    executor: JobExecutorProtocol = Depends(get_job_executor),
) -> DocumentResponse:
    batch = get_batch(session, batch_id)
    if batch is None:
        raise _batch_not_found(batch_id)

    document_id = uuid4()
    paths = _document_paths(settings.storage_root, batch_id, document_id, Path(file.filename or "").suffix or ".pdf")
    job_ids: list[UUID] = []
    try:
        stored = save_upload(file, paths, settings)
        if settings.storage_backend == "s3":
            local_path = settings.storage_root.resolve() / stored.input_relative_path
            get_storage_service(settings).put_file(local_path, stored.input_relative_path)

        split_page_count = _split_page_count(document_type, stored.content_type, paths.input_source_path)
        document = create_document(
            session,
            id=document_id,
            batch_id=batch_id,
            original_filename=file.filename or "",
            safe_filename=stored.stored_filename,
            media_type=stored.content_type,
            size_bytes=stored.file_size_bytes,
            sha256=stored.sha256,
            storage_key=stored.input_relative_path,
            document_type=document_type,
            page_count=split_page_count,
        )

        if split_page_count is not None:
            job_ids = _create_document_page_jobs(
                session,
                settings,
                batch_id=batch_id,
                document=document,
                input_path=paths.input_source_path,
                page_count=split_page_count,
                document_type=document_type,
            )
        else:
            job_id = uuid4()
            create_job(
                session,
                id=job_id,
                batch_id=batch_id,
                document_id=document.id,
                status=JobStatus.PENDING,
                document_type=document_type,
                original_filename=file.filename or "",
                stored_filename=stored.stored_filename,
                content_type=stored.content_type,
                file_size_bytes=stored.file_size_bytes,
                input_relative_path=stored.input_relative_path,
                debug_relative_path=paths.debug_relative_path,
                stdout_log_relative_path=paths.stdout_log_relative_path,
                stderr_log_relative_path=paths.stderr_log_relative_path,
                ocr_git_ref=settings.marriage_ocr_git_ref,
            )
            job_ids = [job_id]
        session.commit()
    except UploadValidationError:
        session.rollback()
        shutil.rmtree(paths.job_root, ignore_errors=True)
        raise
    except Exception as exc:
        session.rollback()
        shutil.rmtree(paths.job_root, ignore_errors=True)
        raise ApiError(500, "INTERNAL_ERROR", "Failed to upload document.") from exc

    submission_failures = 0
    for pending_job_id in job_ids:
        try:
            executor.submit(pending_job_id)
        except Exception:
            submission_failures += 1
            logger.exception("failed to submit OCR job %s for document %s", pending_job_id, document.id)
            mark_failed(
                session,
                pending_job_id,
                "INTERNAL_PROCESSING_ERROR",
                "The OCR job could not be submitted for background processing.",
                datetime.now(UTC),
            )
    if submission_failures:
        recompute_document_status(session, document.id)
        recompute_batch_status(session, batch_id)
        session.commit()
        if submission_failures == len(job_ids):
            raise ApiError(500, "INTERNAL_ERROR", "Failed to submit OCR job.")
    return DocumentResponse.model_validate(document)
