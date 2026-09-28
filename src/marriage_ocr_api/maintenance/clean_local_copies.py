"""One-off cleanup of local working copies already safe in object storage.

    python -m marriage_ocr_api.maintenance.clean_local_copies           # dry run
    python -m marriage_ocr_api.maintenance.clean_local_copies --apply   # delete

For every document whose jobs have ALL completed, and whose source file and
every job output are confirmed present in object storage, removes the local
copies (see storage/local_copies.py). Anything unfinished, failed, or not
confirmed in object storage is left untouched. Refuses to run unless
STORAGE_BACKEND=s3 -- with local storage those files are the originals.
New jobs clean up after themselves (jobs/processing.py::complete_job); this
is for what accumulated before that existed.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.core.config import Settings, get_settings
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.storage.base import StorageService
from marriage_ocr_api.storage.factory import get_storage_service
from marriage_ocr_api.storage.local_copies import directory_size, local_roots_for_job


def plan(
    settings: Settings, session: Session, storage: StorageService
) -> tuple[list[tuple[str, list[Path]]], dict[str, int]]:
    """([(document_id, [local dirs to remove])], skip counts by reason)."""
    storage_root = settings.storage_root.resolve()
    jobs_by_document: defaultdict[UUID, list[OCRJob]] = defaultdict(list)
    for job in session.scalars(select(OCRJob).where(OCRJob.document_id.is_not(None))):
        if job.document_id is not None:
            jobs_by_document[job.document_id].append(job)
    removable: list[tuple[str, list[Path]]] = []
    skipped: defaultdict[str, int] = defaultdict(int)
    for document in session.scalars(select(Document)):
        jobs = jobs_by_document.get(document.id, [])
        if not jobs:
            skipped["no jobs"] += 1
            continue
        if any(job.status != JobStatus.COMPLETED.value for job in jobs):
            skipped["not every job completed"] += 1
            continue
        if not storage.exists(document.storage_key):
            skipped["source not in object storage"] += 1
            continue
        if any(not job.output_relative_path or not storage.exists(job.output_relative_path) for job in jobs):
            skipped["an output not in object storage"] += 1
            continue
        dirs = {storage_root / "batches" / str(document.batch_id) / "documents" / str(document.id)}
        for job in jobs:
            dirs.update(local_roots_for_job(settings, job))
        existing = [d for d in dirs if d.exists()]
        if existing:
            removable.append((str(document.id), existing))
    return removable, dict(skipped)


def main(argv: list[str] | None = None) -> int:
    import shutil

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument(
        "--batch-helpers",
        action="store_true",
        help=(
            "instead: remove Gemini Batch Mode helper copies (gemini-batch/<job>/prepared/*) from object "
            "storage for COMPLETED jobs only -- never documents, pages or outputs"
        ),
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.storage_backend != "s3":
        print("Refusing: STORAGE_BACKEND is not s3, so local files are the originals.", file=sys.stderr)
        return 2
    storage = get_storage_service(settings)
    if args.batch_helpers:
        return _clean_batch_helpers(settings, storage, apply=args.apply)
    with get_session_factory(settings)() as session:
        removable, skipped = plan(settings, session, storage)
    total = 0
    for _, dirs in removable:
        for directory in dirs:
            size = directory_size(directory)
            total += size
            if args.apply:
                shutil.rmtree(directory, ignore_errors=True)
    verb = "Removed" if args.apply else "Would remove"
    print(f"{verb} local copies for {len(removable)} document(s): {total / 1e9:.2f} GB")
    for reason, count in sorted(skipped.items()):
        print(f"  left untouched ({reason}): {count}")
    if not args.apply:
        print("Dry run only -- re-run with --apply to delete.")
    return 0


def _clean_batch_helpers(settings: Settings, storage: StorageService, *, apply: bool) -> int:
    from marriage_ocr_api.jobs.gemini_batch import delete_prepared_from_object_storage

    with get_session_factory(settings)() as session:
        job_ids = list(
            session.scalars(
                select(OCRJob.id).where(
                    OCRJob.status == JobStatus.COMPLETED.value, OCRJob.gemini_batch_stage.is_not(None)
                )
            )
        )
    found = [job_id for job_id in job_ids if storage.exists(f"gemini-batch/{job_id}/prepared/manifest.json")]
    deleted = 0
    if apply:
        for job_id in found:
            deleted += delete_prepared_from_object_storage(settings, job_id)
    verb = "Removed" if apply else "Would remove"
    print(
        f"{verb} Gemini batch helper copies for {len(found)} completed job(s)"
        + (f" ({deleted} objects)" if apply else "")
    )
    print("Only gemini-batch/<job>/prepared/* -- documents, pages and outputs are never touched.")
    if not apply:
        print("Dry run only -- re-run with --batch-helpers --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
