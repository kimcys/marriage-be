"""Queue a skipped-file reclassify for every fetched link of one batch.

The UI's per-link reclassify button is hidden, and classifying hundreds of
files by hand isn't realistic after something upstream broke them in bulk (a
full worker disk failed every ingest). Each link's CLASSIFY_FAILED /
NEEDS_MANUAL_CLASSIFICATION / INGEST_FAILED files are re-classified from their
preserved bytes by a worker (onedrive/service.py::run_skipped_files_reclassify),
which costs a Google Vision call per file.

DRY RUN BY DEFAULT: prints how many files and links match and changes nothing.
Pass --execute to act. --limit (default 3) caps how many LINKS one run queues,
so start small and raise it once the first links come back. Links that are
still FETCHING are skipped; run again later.

scripts/ is not copied into the image, so pipe it into the worker container:
  docker compose --env-file .env.production exec -T worker \
      python - --batch-id <uuid> --limit 3 --execute < scripts/reclassify_batch_skipped_files.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

with contextlib.suppress(NameError):  # no __file__ when piped to `python -` (package is installed in the container)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

import marriage_ocr_api.auth.models  # noqa: E402,F401  (registers every table before commit)
from marriage_ocr_api.api.errors import ApiError  # noqa: E402
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.onedrive.models import OneDriveSubmission  # noqa: E402
from marriage_ocr_api.onedrive.service import _is_reclassifiable, reclassify_skipped_files  # noqa: E402
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, default=3, help="max links per run (default 3; 0 = no limit)")
    parser.add_argument("--execute", action="store_true", help="actually queue the reclassify runs")
    args = parser.parse_args()

    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        stmt = (
            select(OneDriveSubmission)
            .where(OneDriveSubmission.batch_id == args.batch_id)
            .order_by(OneDriveSubmission.created_at)
        )
        busy = 0
        by_status: Counter[str] = Counter()
        submission_ids: list[UUID] = []
        for submission in session.scalars(stmt):
            wanted = [item for item in submission.skipped_files or [] if _is_reclassifiable(item)]
            if not wanted:
                continue
            if submission.status != OneDriveSubmissionStatus.FETCHED.value:
                busy += 1
                continue
            by_status.update(item["status"] for item in wanted)
            submission_ids.append(submission.id)
    selected = submission_ids[: args.limit] if args.limit else submission_ids
    print(
        f"{sum(by_status.values())} reclassifiable file(s) in {len(submission_ids)} link(s); "
        f"{len(selected)} link(s) selected, {busy} link(s) busy"
        + ("" if args.execute else "  [DRY RUN -- pass --execute to act]")
    )
    for status, count in sorted(by_status.items()):
        print(f"  {count:5d}  {status}")
    if not args.execute:
        return

    from marriage_ocr_api.onedrive.celery_executor import CeleryOneDriveExecutor

    executor = CeleryOneDriveExecutor()
    queued = failed = 0
    for submission_id in selected:
        with session_factory() as session:
            try:
                reclassify_skipped_files(session, submission_id, executor)
                session.commit()
            except ApiError as exc:
                session.rollback()
                failed += 1
                print(f"  SKIP {submission_id}: {exc.code}")
                continue
        queued += 1
    print(f"done: {queued} link(s) queued, {failed} skipped")


if __name__ == "__main__":
    main()
