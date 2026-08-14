from __future__ import annotations

import csv
from pathlib import Path
from uuid import UUID

import openpyxl
from sqlalchemy.orm import Session

from marriage_ocr_api.records.repositories import create_record_if_missing

_METADATA_COLUMNS = {
    "Confidence",
    "Status",
    "Created At",
    "Updated At",
    "Status Review",
    "Review Reason",
    "Source File",
    "Source Page",
    "Source Record",
    "Crop Folder",
    "Raw OCR JSON",
}

_TYPED_METADATA_COLUMNS = {
    "Source File",
    "Processing Status",
    "Review Required",
    "Failed Fields",
    "Retry Count",
    "Error Message",
}


def _is_metadata_column(name: str) -> bool:
    return name in _METADATA_COLUMNS or name.startswith("Raw ") or name.endswith(" Raw")


def _is_typed_metadata_column(name: str) -> bool:
    return name in _TYPED_METADATA_COLUMNS


def _record_index_from_source_record(value: object) -> int:
    if value is None:
        return 0
    digits = "".join(char for char in str(value) if char.isdigit())
    return int(digits) if digits else 0


def _validation_issues_from_review_reason(value: object) -> list[str]:
    if not value:
        return []
    return [issue.strip() for issue in str(value).split(";") if issue.strip()]


def import_records_from_xlsx(
    session: Session,
    job_id: UUID,
    xlsx_path: Path,
    *,
    batch_id: UUID | None = None,
    document_id: UUID | None = None,
    override_source_page: int | None = None,
) -> int:
    """Import OCR records from the marriage-ocr CLI's XLSX output.

    The real CLI only ever produces an XLSX (no JSON sidecar), with one row per
    extracted record and a mix of business-data columns and diagnostic/metadata
    columns (Confidence, Source Page, Review Reason, Raw *, etc.).

    override_source_page is set when this job is one page of a document that
    marriage-be split before OCR (see batches/pdf_splitter.py): the CLI always
    reports "Source Page"=1 for a single-page input, which would be wrong for
    every page but the first, so the real original page number wins instead.
    """
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return 0
        columns = [str(cell) if cell is not None else "" for cell in header]

        created = 0
        for row_index, row in enumerate(rows, start=1):
            record = dict(zip(columns, row, strict=False))
            if not any(value not in (None, "") for value in record.values()):
                continue

            field_values = {
                key: value
                for key, value in record.items()
                if key and not _is_metadata_column(key) and value is not None
            }
            source_file = record.get("Source File")
            source_record = record.get("Source Record")
            source_page_raw = record.get("Source Page")
            parsed_source_page = int(source_page_raw) if isinstance(source_page_raw, int | float) else None
            source_page = override_source_page if override_source_page is not None else parsed_source_page
            if source_record:
                # source_record disambiguates rows the CLI already distinguished explicitly.
                source_key = f"{source_file}:p{source_page}:{source_record}"
            elif source_file:
                # No source_record: fall back to the sheet row index so that multiple
                # records on the same page with a blank "Source Record" cell don't
                # collapse onto the same key and get silently dropped as duplicates.
                source_key = f"{source_file}:p{source_page}:row-{row_index}"
            else:
                source_key = f"row-{row_index}"
            confidence_raw = record.get("Confidence")
            confidence = float(confidence_raw) if isinstance(confidence_raw, int | float) else None

            if create_record_if_missing(
                session,
                job_id=job_id,
                batch_id=batch_id,
                document_id=document_id,
                payload={
                    "source_key": source_key,
                    "source_page": source_page,
                    "source_record_index": _record_index_from_source_record(source_record),
                    "field_values": field_values,
                    "confidence": confidence,
                    "validation_issues": _validation_issues_from_review_reason(record.get("Review Reason")),
                },
            ):
                created += 1
        return created
    finally:
        workbook.close()


def _typed_validation_issues(record: dict[str, object]) -> list[str]:
    issues: list[str] = []
    review_required = str(record.get("Review Required") or "").strip().lower()
    if review_required in {"true", "1", "yes"}:
        issues.append("review required")
    failed_fields = str(record.get("Failed Fields") or "").strip()
    if failed_fields:
        issues.extend(field.strip() for field in failed_fields.split(",") if field.strip())
    error_message = str(record.get("Error Message") or "").strip()
    if error_message:
        issues.append(error_message)
    return issues


def import_records_from_csv(
    session: Session,
    job_id: UUID,
    csv_path: Path,
    *,
    batch_id: UUID | None = None,
    document_id: UUID | None = None,
    override_source_page: int | None = None,
) -> int:
    """Import OCR records from the marriage-ocr CLI's typed (`process-typed`) CSV output.

    The typed pipeline writes one row per source PDF (no page/record splitting) and
    a different metadata column set than the handwritten XLSX output (no numeric
    Confidence column; "Processing Status"/"Review Required"/"Failed Fields" instead).
    Typed documents are never split into per-page jobs (see batches/pdf_splitter.py),
    so override_source_page is accepted only for signature symmetry with the XLSX
    importer and is expected to always be None here.
    """
    created = 0
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_index, record in enumerate(reader, start=1):
            if not any(value not in (None, "") for value in record.values()):
                continue

            field_values = {
                key: value
                for key, value in record.items()
                if key and not _is_typed_metadata_column(key) and value not in (None, "")
            }
            source_file = record.get("Source File")
            source_key = f"{source_file}" if source_file else f"row-{row_index}"

            if create_record_if_missing(
                session,
                job_id=job_id,
                batch_id=batch_id,
                document_id=document_id,
                payload={
                    "source_key": source_key,
                    "source_page": override_source_page,
                    "source_record_index": 0,
                    "field_values": field_values,
                    "confidence": None,
                    "validation_issues": _typed_validation_issues(record),
                },
            ):
                created += 1
    return created
