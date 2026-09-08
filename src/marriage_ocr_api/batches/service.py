from __future__ import annotations

import contextlib
import shutil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Document, Export
from marriage_ocr_api.batches.repositories import delete_batch_row
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.storage.factory import get_storage_service


def delete_batch(session: Session, settings: Settings, batch_id: UUID) -> None:
    """Deletes a batch and everything scoped to it -- documents, OCR jobs,
    records (+ their revisions), OneDrive submissions, and exports -- plus
    their stored files. Irreversible; the caller (the API route) is the
    one place a confirmation should already have happened.
    """
    job_ids = list(session.scalars(select(OCRJob.id).where(OCRJob.batch_id == batch_id)))
    document_keys = [key for key in session.scalars(select(Document.storage_key).where(Document.batch_id == batch_id))]
    job_output_keys = [
        key
        for key in session.scalars(
            select(OCRJob.output_relative_path).where(
                OCRJob.batch_id == batch_id, OCRJob.output_relative_path.is_not(None)
            )
        )
        if key
    ]
    export_keys = [
        key
        for key in session.scalars(
            select(Export.storage_key).where(Export.batch_id == batch_id, Export.storage_key.is_not(None))
        )
        if key
    ]

    delete_batch_row(session, batch_id)
    session.commit()

    if settings.storage_backend == "s3":
        storage = get_storage_service(settings)
        for key in (*document_keys, *job_output_keys, *export_keys):
            with contextlib.suppress(FileNotFoundError):
                storage.delete(key)

    # Local disk cleanup always runs, even under STORAGE_BACKEND=s3 -- debug
    # artifacts and log files are never pushed to S3 (only the input/output
    # files referenced above are), so they only ever exist on local disk.
    storage_root = settings.storage_root.resolve()
    shutil.rmtree(storage_root / "batches" / str(batch_id), ignore_errors=True)
    # A split multi-page document's per-page jobs live under their own
    # storage_root/jobs/{job_id}/ tree, entirely separate from
    # storage_root/batches/{batch_id}/ -- see batches/document_ingest.py.
    for job_id in job_ids:
        shutil.rmtree(storage_root / "jobs" / str(job_id), ignore_errors=True)
