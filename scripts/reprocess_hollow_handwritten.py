"""Re-run OCR for handwritten pages whose records all came back hollow.

A "hollow" record has no name, no IC and no date -- usually only a Bil and a phone number --
although the page itself has them (the reader failed to capture the row). Approving those would
hide real records, so the page is read again instead.

DRY RUN BY DEFAULT. A job is eligible when it is COMPLETED, of one of --type, and EVERY record on
it is PENDING_REVIEW, untouched by a human (version 1, no reviewer) and hollow. A page with a
good record on it is left alone, so a worse re-read can never replace data that is already right.
--execute deletes the job's records (and revisions), resets it to PENDING and enqueues it on
--queue, exactly like scripts/reprocess_typed_jobs.py.

  docker compose --env-file .env.production exec -T api \
      python - --type HANDWRITTEN_CERAI_LEGACY --limit 30 < scripts/reprocess_hollow_handwritten.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

with contextlib.suppress(NameError):  # no __file__ when piped to `python -`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete, select  # noqa: E402

from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db import repositories as job_repositories  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.jobs.status import JobStatus  # noqa: E402
from marriage_ocr_api.records.models import OCRRecord, RecordRevision  # noqa: E402
from marriage_ocr_api.storage.factory import get_storage_service  # noqa: E402

NOT_RECORDED = {"", "TIADA MAKLUMAT", "TIADA", "-"}
IDENTITY_FIELDS = ("Nama Suami", "Nama Isteri", "IC Suami", "IC Isteri", "Tarikh Cerai", "Tarikh Rujuk")


def is_hollow(field_values: dict | None) -> bool:
    values = field_values or {}
    return all(str(values.get(field) or "").strip().upper() in NOT_RECORDED for field in IDENTITY_FIELDS)


def _eligible_job_ids(session, types: list[str], limit: int) -> list[UUID]:
    stmt = (
        select(OCRJob.id)
        .where(OCRJob.status == JobStatus.COMPLETED.value, OCRJob.document_type.in_(types))
        .order_by(OCRJob.created_at)
    )
    eligible: list[UUID] = []
    for job_id in session.scalars(stmt):
        records = session.execute(
            select(OCRRecord.status, OCRRecord.version, OCRRecord.reviewed_by, OCRRecord.field_values).where(
                OCRRecord.job_id == job_id
            )
        ).all()
        if records and all(
            status == "PENDING_REVIEW" and version == 1 and reviewer is None and is_hollow(values)
            for status, version, reviewer, values in records
        ):
            eligible.append(job_id)
            if limit and len(eligible) >= limit:
                break
    return eligible


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", action="append", required=True, help="document type (repeatable)")
    parser.add_argument("--limit", type=int, default=30, help="max jobs (default 30; 0 = no limit)")
    parser.add_argument("--queue", default="ocr_reprocess")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    session_factory = get_session_factory(get_settings())
    storage = get_storage_service(get_settings())
    with session_factory() as session:
        job_ids = _eligible_job_ids(session, args.type, args.limit)
    print(f"{len(job_ids)} eligible job(s)" + ("" if args.execute else "  [DRY RUN -- pass --execute to act]"))
    if not args.execute:
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
            job.error_code = job.error_message = None
            job.started_at = job.completed_at = None
            job.gemini_batch_stage = job.gemini_batch_name = job.gemini_batch_updated_at = None
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
        print(f"  re-enqueued {job_id} (removed {removed} record(s))")
    print(f"done: {done} re-enqueued, {skipped} skipped")


if __name__ == "__main__":
    main()
