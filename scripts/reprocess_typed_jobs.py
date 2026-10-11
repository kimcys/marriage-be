"""Re-run OCR for typed jobs whose records only need review because Mas Kahwin
was lost (see typed/normalizer.py::normalize_mas_kahwin).

DRY RUN BY DEFAULT: prints what it would do and changes nothing. Pass
--execute to act. For each eligible job, --execute:
  1. checks the original is still in object storage (skips the job if not),
  2. deletes that job's records (and revisions) -- the importer skips keys that
     already exist, so a re-run could not otherwise replace them,
  3. resets the job to PENDING and enqueues it on Celery, like the retry button.
Only originals' derived rows change: nothing in Spaces is deleted, and the
Gemini-batch helper copies are handled by the normal job flow.

Eligible = COMPLETED typed job where EVERY record is untouched by a human
(version 1, no reviewer), still PENDING_REVIEW, and lists "Mas Kahwin" in
missing_fields. A job with any edited or approved record is skipped.

Run inside the api container, e.g.
  docker compose --env-file .env.production exec -T api \
      python scripts/reprocess_typed_jobs.py --created-on 2026-09-26 --limit 20
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete, select  # noqa: E402

from marriage_ocr_api.batches.status import TYPED_DOCUMENT_TYPES  # noqa: E402
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db import repositories as job_repositories  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.jobs.status import JobStatus  # noqa: E402
from marriage_ocr_api.records.models import OCRRecord, RecordRevision  # noqa: E402
from marriage_ocr_api.storage.factory import get_storage_service  # noqa: E402

FIELD = "Mas Kahwin"


def _eligible_job_ids(session, args) -> list[UUID]:
    typed = [t.value if hasattr(t, "value") else str(t) for t in TYPED_DOCUMENT_TYPES]
    stmt = select(OCRJob.id).where(OCRJob.status == JobStatus.COMPLETED.value, OCRJob.document_type.in_(typed))
    if args.batch_id:
        stmt = stmt.where(OCRJob.batch_id.in_(args.batch_id))
    if args.created_on:
        start = datetime.combine(args.created_on, datetime.min.time(), tzinfo=UTC)
        stmt = stmt.where(OCRJob.created_at >= start, OCRJob.created_at < start + timedelta(days=1))
    stmt = stmt.order_by(OCRJob.created_at)

    eligible: list[UUID] = []
    for job_id in session.scalars(stmt):
        records = session.execute(
            select(OCRRecord.status, OCRRecord.version, OCRRecord.reviewed_by, OCRRecord.missing_fields).where(
                OCRRecord.job_id == job_id
            )
        ).all()
        if not records:
            continue
        if all(
            status == "PENDING_REVIEW" and version == 1 and reviewer is None and FIELD in (missing or [])
            for status, version, reviewer, missing in records
        ):
            eligible.append(job_id)
            if args.limit and len(eligible) >= args.limit:
                break
    return eligible


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, action="append", help="limit to this batch (repeatable)")
    parser.add_argument("--created-on", type=date.fromisoformat, help="limit to jobs created that UTC day")
    parser.add_argument("--limit", type=int, default=20, help="max jobs per run (default 20; 0 = no limit)")
    parser.add_argument("--execute", action="store_true", help="actually delete records and re-enqueue")
    args = parser.parse_args()

    settings = get_settings()
    session_factory = get_session_factory(settings)
    storage = get_storage_service(settings)

    with session_factory() as session:
        job_ids = _eligible_job_ids(session, args)
    print(f"{len(job_ids)} eligible job(s)" + ("" if args.execute else "  [DRY RUN -- pass --execute to act]"))
    if not args.execute:
        for job_id in job_ids[:10]:
            print("  would re-run", job_id)
        return

    from marriage_ocr_api.jobs.tasks import run_ocr_job

    done = skipped = 0
    for job_id in job_ids:
        with session_factory() as session:
            job = session.get(OCRJob, job_id)
            if job is None or not storage.exists(job.input_relative_path):
                print("  SKIP (original not in storage)", job_id)
                skipped += 1
                continue
            record_ids = select(OCRRecord.id).where(OCRRecord.job_id == job_id)
            session.execute(delete(RecordRevision).where(RecordRevision.record_id.in_(record_ids)))
            removed = session.execute(delete(OCRRecord).where(OCRRecord.job_id == job_id)).rowcount
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
            run_ocr_job.delay(str(job_id))
        except Exception as exc:  # leave it FAILED so the UI retry button still works
            with session_factory() as session:
                job_repositories.mark_failed(
                    session, job_id, "INTERNAL_PROCESSING_ERROR", f"re-enqueue failed: {exc}"[:900], datetime.now(UTC)
                )
                session.commit()
            print("  ENQUEUE FAILED", job_id, exc)
            continue
        done += 1
        print(f"  re-enqueued {job_id} (removed {removed} record(s))")
    print(f"done: {done} re-enqueued, {skipped} skipped")


if __name__ == "__main__":
    main()
