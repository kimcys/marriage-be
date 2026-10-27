"""Re-enqueue PENDING jobs of one batch whose Celery message is gone.

A job can sit PENDING in the database with no message in the broker (the queue
was lost or flushed, or the enqueue never happened). Nothing recovers those:
the stale-job beat task only handles PROCESSING jobs, so the document and batch
stay PROCESSING forever. Batch 61e5d9fc had 232 such jobs for eight days.

DRY RUN BY DEFAULT: prints how many jobs match and changes nothing. Pass
--execute to act. --limit (default 5) caps one run, so enqueue a few first, wait
to see them complete, then raise it. Only jobs PENDING for longer than
--min-age-minutes are touched, so a job that was just enqueued normally is left
alone. Job state is not modified; run_ocr_job claims PENDING -> PROCESSING itself.

scripts/ is not copied into the image, so pipe it into the worker container:
  docker compose --env-file .env.production exec -T worker \
      python - --batch-id <uuid> --limit 5 --execute < scripts/requeue_pending_jobs.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

with contextlib.suppress(NameError):  # no __file__ when piped to `python -` (package is installed in the container)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

import marriage_ocr_api.auth.models  # noqa: E402, F401  (register every table before the session is used)
import marriage_ocr_api.batches.models  # noqa: E402, F401
import marriage_ocr_api.onedrive.models  # noqa: E402, F401
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.jobs.status import JobStatus  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, required=True)
    parser.add_argument("--min-age-minutes", type=int, default=60, help="only jobs PENDING at least this long")
    parser.add_argument("--limit", type=int, default=5, help="max jobs per run (default 5; 0 = no limit)")
    parser.add_argument("--queue", help="Celery queue to send the jobs to (default: the normal ocr_jobs queue)")
    parser.add_argument("--execute", action="store_true", help="actually enqueue")
    args = parser.parse_args()

    cutoff = datetime.now(UTC) - timedelta(minutes=args.min_age_minutes)
    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        stmt = select(OCRJob.id).where(
            OCRJob.batch_id == args.batch_id,
            OCRJob.status == JobStatus.PENDING.value,
            OCRJob.updated_at < cutoff,
        )
        all_ids = list(session.scalars(stmt.order_by(OCRJob.updated_at)))
    job_ids = all_ids[: args.limit] if args.limit else all_ids
    print(
        f"{len(all_ids)} stale PENDING job(s) match; {len(job_ids)} selected"
        + ("" if args.execute else "  [DRY RUN -- pass --execute to act]")
    )
    if not args.execute:
        return

    from marriage_ocr_api.jobs.tasks import run_ocr_job

    done = 0
    for job_id in job_ids:
        try:
            run_ocr_job.apply_async(args=[str(job_id)], queue=args.queue)
        except Exception as exc:  # job stays PENDING, so a rerun picks it up again
            print("  ENQUEUE FAILED", job_id, exc)
            continue
        done += 1
    print(f"done: {done} enqueued")


if __name__ == "__main__":
    main()
