"""Re-type FAILED jobs whose input is an image that was classified as a typed
document, then re-run them.

process-typed only reads PDFs, so an image routed to a typed document type
fails with "Typed input must be a PDF or directory" on every retry (see
onedrive/service.py::_ingest_one_file, which now refuses to create these).
This repairs the ones created before that guard.

DRY RUN BY DEFAULT: prints the matching jobs and changes nothing. Pass
--execute to act. For each matching job, --execute:
  1. checks the original is still in object storage (skips the job if not),
  2. sets the job's and its document's document_type to --to,
  3. resets the job to PENDING and enqueues it on Celery, like the retry button.
Nothing in Spaces is touched.

Eligible = FAILED job with a typed document_type and a non-PDF input. Always
pass --job-id or --batch-id plus --to: the right handwritten type depends on
what the page actually is, so look at the image first.

scripts/ is not copied into the image, so pipe it into the worker container:
  docker compose --env-file .env.production exec -T worker \
      python - --job-id <uuid> --to HANDWRITTEN_RUJUK_LEGACY < scripts/fix_typed_image_jobs.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

with contextlib.suppress(NameError):  # no __file__ when piped to `python -` (package is installed in the container)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from marriage_ocr_api.batches.models import Document  # noqa: E402
from marriage_ocr_api.batches.status import HANDWRITTEN_DOCUMENT_TYPES, TYPED_DOCUMENT_TYPES, DocumentType  # noqa: E402
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db import repositories as job_repositories  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.jobs.status import JobStatus  # noqa: E402
from marriage_ocr_api.storage.factory import get_storage_service  # noqa: E402


def _eligible_jobs(session, args) -> list[tuple[UUID, str, str, str]]:
    typed = [t.value for t in TYPED_DOCUMENT_TYPES]
    stmt = select(OCRJob.id, OCRJob.original_filename, OCRJob.document_type, OCRJob.input_relative_path).where(
        OCRJob.status == JobStatus.FAILED.value, OCRJob.document_type.in_(typed)
    )
    if args.job_id:
        stmt = stmt.where(OCRJob.id.in_(args.job_id))
    if args.batch_id:
        stmt = stmt.where(OCRJob.batch_id.in_(args.batch_id))
    rows = session.execute(stmt.order_by(OCRJob.created_at)).all()
    return [row for row in rows if not row.input_relative_path.lower().endswith(".pdf")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, action="append", help="limit to this batch (repeatable)")
    parser.add_argument("--job-id", type=UUID, action="append", help="limit to these jobs (repeatable)")
    parser.add_argument(
        "--to", required=True, help="handwritten DocumentType to re-type to, e.g. HANDWRITTEN_RUJUK_LEGACY"
    )
    parser.add_argument("--queue", help="Celery queue to send the jobs to (default: the normal ocr_jobs queue)")
    parser.add_argument("--execute", action="store_true", help="actually re-type and re-enqueue")
    args = parser.parse_args()
    if not args.job_id and not args.batch_id:
        parser.error("pass --job-id and/or --batch-id")
    new_type = DocumentType(args.to)
    if new_type not in HANDWRITTEN_DOCUMENT_TYPES:
        parser.error(f"--to must be a handwritten type, got {args.to}")

    settings = get_settings()
    session_factory = get_session_factory(settings)
    storage = get_storage_service(settings)

    with session_factory() as session:
        jobs = _eligible_jobs(session, args)
    print(f"{len(jobs)} eligible job(s)" + ("" if args.execute else "  [DRY RUN -- pass --execute to act]"))
    if not args.execute:
        for job_id, filename, document_type, _ in jobs[:50]:
            print(f"  would re-type {job_id} {filename}: {document_type} -> {new_type.value}")
        return

    from marriage_ocr_api.jobs.tasks import run_ocr_job

    done = skipped = 0
    for job_id, _, _, _ in jobs:
        with session_factory() as session:
            job = session.get(OCRJob, job_id)
            if job is None or not storage.exists(job.input_relative_path):
                print("  SKIP (original not in storage)", job_id)
                skipped += 1
                continue
            job.document_type = new_type.value
            if job.document_id is not None:
                document = session.get(Document, job.document_id)
                if document is not None:
                    document.document_type = new_type.value
            job.status = JobStatus.PENDING.value
            job.error_code = None
            job.error_message = None
            job.started_at = None
            job.completed_at = None
            job.gemini_batch_stage = None
            job.gemini_batch_name = None
            job.gemini_batch_updated_at = None
            session.commit()
        try:
            run_ocr_job.apply_async(args=[str(job_id)], queue=args.queue)
        except Exception as exc:  # leave it FAILED so the UI retry button still works
            with session_factory() as session:
                job_repositories.mark_failed(
                    session, job_id, "INTERNAL_PROCESSING_ERROR", f"re-enqueue failed: {exc}"[:900], datetime.now(UTC)
                )
                session.commit()
            print("  ENQUEUE FAILED", job_id, exc)
            continue
        done += 1
        print(f"  re-typed and re-enqueued {job_id}")
    print(f"done: {done} re-enqueued, {skipped} skipped")


if __name__ == "__main__":
    main()
