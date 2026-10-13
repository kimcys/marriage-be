"""Re-queue FAILED jobs of one batch, a few at a time -- the bulk version of the
UI's retry button (jobs/service.py::retry_job), for when something upstream
(e.g. the Gemini credit ran out) failed many jobs at once.

DRY RUN BY DEFAULT: prints how many jobs match and changes nothing. Pass
--execute to act. --limit (default 5) caps one run, so retry a few first, wait
to see them complete, then raise it. Jobs are enqueued oldest-failure first.

scripts/ is not copied into the image, so pipe it into the worker container:
  docker compose --env-file .env.production exec -T worker \
      python - --batch-id <uuid> --limit 5 --execute < scripts/retry_failed_jobs.py
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

from marriage_ocr_api.batches.repositories import recompute_batch_status, recompute_document_status  # noqa: E402
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db import repositories as job_repositories  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.jobs.status import JobStatus  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, required=True)
    parser.add_argument("--error-code", help="only jobs failed with this error_code, e.g. OCR_PROCESS_FAILED")
    parser.add_argument("--limit", type=int, default=5, help="max jobs per run (default 5; 0 = no limit)")
    parser.add_argument("--queue", help="Celery queue to send the jobs to (default: the normal ocr_jobs queue)")
    parser.add_argument("--execute", action="store_true", help="actually reset and re-enqueue")
    args = parser.parse_args()

    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        stmt = select(OCRJob.id).where(OCRJob.batch_id == args.batch_id, OCRJob.status == JobStatus.FAILED.value)
        if args.error_code:
            stmt = stmt.where(OCRJob.error_code == args.error_code)
        all_ids = list(session.scalars(stmt.order_by(OCRJob.updated_at)))
    job_ids = all_ids[: args.limit] if args.limit else all_ids
    print(
        f"{len(all_ids)} failed job(s) match; {len(job_ids)} selected"
        + ("" if args.execute else "  [DRY RUN -- pass --execute to act]")
    )
    if not args.execute:
        return

    from marriage_ocr_api.jobs.tasks import run_ocr_job

    done = 0
    for job_id in job_ids:
        with session_factory() as session:
            job = job_repositories.mark_pending_for_retry(session, job_id)
            if job.document_id is not None:
                recompute_document_status(session, job.document_id)
            recompute_batch_status(session, args.batch_id)
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
    print(f"done: {done} re-enqueued")


if __name__ == "__main__":
    main()
