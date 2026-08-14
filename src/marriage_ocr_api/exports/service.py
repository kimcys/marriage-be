from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Export
from marriage_ocr_api.batches.repositories import create_export as create_export_record
from marriage_ocr_api.batches.repositories import delete_export as delete_export_record
from marriage_ocr_api.batches.repositories import list_stale_exports
from marriage_ocr_api.batches.schemas import ExportFormat
from marriage_ocr_api.batches.status import ExportStatus
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.exports.writer import write_csv_export, write_xlsx_export
from marriage_ocr_api.records.models import OCRRecord
from marriage_ocr_api.records.status import RecordStatus
from marriage_ocr_api.storage.factory import get_storage_service


def utcnow() -> datetime:
    return datetime.now(UTC)


def _is_exportable(record: OCRRecord) -> bool:
    return record.status == RecordStatus.APPROVED.value


def _effective_values(record: OCRRecord) -> dict[str, object]:
    values: dict[str, object] = {}
    if record.normalized_data:
        values.update(record.normalized_data)
    elif record.field_values:
        values.update(record.field_values)
    if record.corrected_data:
        values.update(record.corrected_data)
    return values


def build_export_rows(
    session: Session,
    batch_id: UUID,
    include_unreviewed: bool,
) -> tuple[list[str], list[dict[str, object]]]:
    stmt: Select[tuple[OCRRecord]] = select(OCRRecord).where(OCRRecord.batch_id == batch_id)
    stmt = stmt.order_by(
        OCRRecord.document_id.asc().nulls_last(),
        OCRRecord.source_page.asc().nulls_last(),
        OCRRecord.source_record_index.asc(),
        OCRRecord.created_at.asc(),
        OCRRecord.id.asc(),
    )
    records = list(session.scalars(stmt))
    columns: list[str] = []
    rows: list[dict[str, object]] = []
    for record in records:
        if not include_unreviewed and not _is_exportable(record):
            continue
        effective = _effective_values(record)
        for key in effective:
            if key not in columns:
                columns.append(key)
        rows.append(effective)

    if not columns:
        return [], []

    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        ordered = OrderedDict()
        for column in columns:
            ordered[column] = row.get(column, "")
        normalized_rows.append(dict(ordered))
    return columns, normalized_rows


def _export_filename(export_format: ExportFormat) -> str:
    return "records.csv" if export_format == ExportFormat.CSV else "records.xlsx"


def create_export_artifact(
    session: Session,
    settings: Settings,
    *,
    batch_id: UUID,
    format: ExportFormat,
    include_unreviewed: bool,
    created_by: UUID | None,
) -> Export:
    export = create_export_record(
        session,
        batch_id=batch_id,
        format=format,
        created_by=created_by,
    )
    session.flush()
    try:
        columns, rows = build_export_rows(session, batch_id, include_unreviewed)
        storage = get_storage_service(settings)
        filename = _export_filename(format)
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / filename
            if format == ExportFormat.CSV:
                write_csv_export(temp_path, rows=rows, columns=columns)
            else:
                write_xlsx_export(temp_path, rows=rows, columns=columns)
            stored = storage.put_file(temp_path, f"exports/{export.id}/{filename}")
        export.status = ExportStatus.COMPLETED.value
        export.storage_key = stored.key
        export.record_count = len(rows)
        export.completed_at = utcnow()
        if not rows:
            # A batch with nothing approved yet (or no records at all)
            # otherwise "succeeds" silently with a header-less, zero-row
            # file -- error_message is nullable and only ever set on
            # FAILED exports today, so this is safe to also use here as an
            # informational note on an otherwise-COMPLETED export.
            export.error_message = (
                "No records matched the export criteria (e.g. none approved yet)."
                if not include_unreviewed
                else "This batch has no records yet."
            )
        session.commit()
    except Exception as exc:
        export.status = ExportStatus.FAILED.value
        export.error_message = str(exc)
        export.completed_at = utcnow()
        session.commit()
        raise
    return export


def build_export_download_response(export: Export, settings: Settings) -> FileResponse | RedirectResponse:
    storage = get_storage_service(settings)
    if export.storage_key is None:
        raise FileNotFoundError("export file missing")
    signed_url = storage.signed_download_url(export.storage_key, expires_seconds=300)
    if signed_url is not None:
        return RedirectResponse(signed_url)
    file_path = settings.storage_root.resolve() / export.storage_key
    if not file_path.exists():
        raise FileNotFoundError("export file missing")
    media_type = (
        "text/csv; charset=utf-8"
        if export.format == ExportFormat.CSV.value
        else ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    )
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=_export_filename(ExportFormat(export.format)),
        headers={"X-Content-Type-Options": "nosniff"},
    )


def delete_export_artifact(session: Session, settings: Settings, export: Export) -> None:
    """Delete an export's stored file (if any) and its DB row.

    Missing/already-gone files are tolerated -- deleting an export whose
    file was already cleaned up (or never finished writing) must still
    succeed and remove the DB row, not leave a dangling reference behind.
    """
    if export.storage_key is not None:
        storage = get_storage_service(settings)
        try:
            storage.delete(export.storage_key)
        except FileNotFoundError:
            pass
    delete_export_record(session, export)
    session.commit()


def cleanup_stale_exports(session: Session, settings: Settings, retention_days: float) -> int:
    """Delete exports older than the retention window.

    Nothing else in this codebase ever deletes an export -- every export
    request writes a brand-new file (local disk or S3) and DB row with no
    expiry. At 1M-record scale with routine re-exports during review, that
    is unbounded storage growth from day one. Runs on the beat schedule
    configured in celery_app.py.
    """
    cutoff = utcnow() - timedelta(days=retention_days)
    stale = list_stale_exports(session, cutoff)
    for export in stale:
        delete_export_artifact(session, settings, export)
    return len(stale)
