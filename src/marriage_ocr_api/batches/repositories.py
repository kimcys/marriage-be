from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Batch, Document, Export
from marriage_ocr_api.batches.schemas import ExportFormat
from marriage_ocr_api.batches.status import BatchStatus, DocumentStatus, DocumentType, ExportStatus
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.status import JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def create_batch(
    session: Session,
    *,
    name: str,
    description: str | None,
    created_by: UUID | None,
    status: BatchStatus = BatchStatus.DRAFT,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Batch:
    now = utcnow()
    batch = Batch(
        name=name,
        description=description,
        status=status.value,
        created_by=created_by,
        started_at=started_at,
        completed_at=completed_at,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    session.add(batch)
    session.flush()
    return batch


def create_document(
    session: Session,
    *,
    id: UUID | None = None,
    batch_id: UUID,
    original_filename: str,
    safe_filename: str,
    media_type: str,
    size_bytes: int,
    sha256: str,
    storage_key: str,
    status: DocumentStatus = DocumentStatus.UPLOADED,
    document_type: DocumentType = DocumentType.HANDWRITTEN_REGISTER,
    page_count: int | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Document:
    now = utcnow()
    document = Document(
        id=id or uuid4(),
        batch_id=batch_id,
        original_filename=original_filename,
        safe_filename=safe_filename,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_key=storage_key,
        status=status.value,
        document_type=document_type.value,
        page_count=page_count,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    session.add(document)
    session.flush()
    return document


def create_export(
    session: Session,
    *,
    batch_id: UUID,
    format: str | ExportFormat,
    created_by: UUID | None,
    status: ExportStatus = ExportStatus.PENDING,
    storage_key: str | None = None,
    record_count: int | None = None,
    error_message: str | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> Export:
    now = utcnow()
    export = Export(
        batch_id=batch_id,
        format=format.value if isinstance(format, ExportFormat) else str(format),
        status=status.value,
        storage_key=storage_key,
        record_count=record_count,
        created_by=created_by,
        error_message=error_message,
        created_at=created_at or now,
        completed_at=completed_at,
    )
    session.add(export)
    session.flush()
    return export


def get_batch(session: Session, batch_id: UUID) -> Batch | None:
    return session.get(Batch, batch_id)


def list_batches(session: Session, limit: int, offset: int) -> list[Batch]:
    stmt: Select[tuple[Batch]] = (
        select(Batch).order_by(Batch.created_at.desc(), Batch.id.desc()).limit(limit).offset(offset)
    )
    return list(session.scalars(stmt))


def count_batches(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Batch)) or 0)


def get_document(session: Session, document_id: UUID) -> Document | None:
    return session.get(Document, document_id)


def list_documents(session: Session, batch_id: UUID, limit: int, offset: int) -> list[Document]:
    stmt: Select[tuple[Document]] = select(Document).where(Document.batch_id == batch_id)
    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def mark_document_status(session: Session, document_id: UUID, status: DocumentStatus) -> None:
    document = session.get(Document, document_id)
    if document is None:
        return
    document.status = status.value
    document.updated_at = utcnow()
    session.flush()


def recompute_document_status(session: Session, document_id: UUID) -> None:
    """Aggregate a document's status from all of its jobs (one job per page for
    split documents, or a single job otherwise).

    A document reaches PROCESSED once at least one job has completed and none
    are still pending/processing -- a partially-failed document still yields a
    usable, exportable set of records. Only a document where every single job
    failed is marked FAILED. Individual page failures stay visible at the job
    level (see the document_id/batch_id filters on GET /api/v1/jobs) rather
    than being hidden behind an all-or-nothing document status.
    """
    document = session.get(Document, document_id)
    if document is None:
        return
    job_statuses = list(session.scalars(select(OCRJob.status).where(OCRJob.document_id == document_id)))
    if not job_statuses:
        return

    pending_or_processing = {JobStatus.PENDING.value, JobStatus.PROCESSING.value}
    if any(status in pending_or_processing for status in job_statuses):
        next_status = DocumentStatus.PROCESSING
    elif all(status == JobStatus.FAILED.value for status in job_statuses):
        next_status = DocumentStatus.FAILED
    else:
        next_status = DocumentStatus.PROCESSED

    if document.status == next_status.value:
        return
    document.status = next_status.value
    document.updated_at = utcnow()
    session.flush()


def recompute_batch_status(session: Session, batch_id: UUID) -> None:
    batch = session.get(Batch, batch_id)
    if batch is None:
        return
    document_statuses = {
        document.status for document in session.scalars(select(Document).where(Document.batch_id == batch_id))
    }
    if not document_statuses:
        return
    if document_statuses == {DocumentStatus.PROCESSED.value}:
        next_status = BatchStatus.COMPLETED
    elif DocumentStatus.FAILED.value in document_statuses:
        next_status = BatchStatus.FAILED
    elif document_statuses & {DocumentStatus.QUEUED.value, DocumentStatus.PROCESSING.value}:
        next_status = BatchStatus.PROCESSING
    else:
        next_status = BatchStatus.DRAFT
    if batch.status == next_status.value:
        return
    batch.status = next_status.value
    batch.updated_at = utcnow()
    if next_status == BatchStatus.PROCESSING and batch.started_at is None:
        batch.started_at = utcnow()
    if next_status in (BatchStatus.COMPLETED, BatchStatus.FAILED):
        batch.completed_at = utcnow()
    session.flush()


def count_documents(session: Session, batch_id: UUID) -> int:
    return int(session.scalar(select(func.count()).select_from(Document).where(Document.batch_id == batch_id)) or 0)


def get_export(session: Session, export_id: UUID) -> Export | None:
    return session.get(Export, export_id)


def list_exports(session: Session, limit: int, offset: int) -> list[Export]:
    stmt: Select[tuple[Export]] = (
        select(Export).order_by(Export.created_at.desc(), Export.id.desc()).limit(limit).offset(offset)
    )
    return list(session.scalars(stmt))


def count_exports(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Export)) or 0)


def delete_export(session: Session, export: Export) -> None:
    session.delete(export)
    session.flush()


def list_stale_exports(session: Session, older_than: datetime) -> list[Export]:
    """Exports older than the retention window, for periodic cleanup.

    Every export write creates a brand-new file (local disk or S3) and DB
    row with no expiry -- nothing else in this codebase ever deletes an
    export, so at 1M-record scale with routine re-exports during review,
    this is unbounded storage growth from day one.
    """
    stmt: Select[tuple[Export]] = select(Export).where(Export.created_at < older_than)
    return list(session.scalars(stmt))
