"""Classify NEEDS_MANUAL_CLASSIFICATION skipped files as handwritten Nikah in
batches whose name contains "nikah".

onedrive/service.py::_apply_nikah_batch_fallback now does this for new
fetches; this applies it to files skipped before it existed, through the same
path as the UI's per-file "Classify" button (classify_skipped_file) -- so a
file whose preserved bytes are gone is queued for re-download instead.

DRY RUN BY DEFAULT: prints how many files match per batch and changes
nothing. Pass --execute to act. --limit (default 20) caps one run, so try a
few first and check the jobs appear in the batch before raising it. Links
that are still FETCHING are skipped (classify refuses them); run again later.

scripts/ is not copied into the image, so pipe it into the worker container:
  docker compose --env-file .env.production exec -T worker \
      python - --limit 20 --execute < scripts/classify_nikah_skipped_files.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from uuid import UUID

with contextlib.suppress(NameError):  # no __file__ when piped to `python -` (package is installed in the container)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

import marriage_ocr_api.auth.models  # noqa: E402,F401  (registers every table before commit)
from marriage_ocr_api.api.errors import ApiError  # noqa: E402
from marriage_ocr_api.batches.models import Batch  # noqa: E402
from marriage_ocr_api.batches.status import DocumentType  # noqa: E402
from marriage_ocr_api.core.config import get_settings  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.onedrive.models import OneDriveSubmission  # noqa: E402
from marriage_ocr_api.onedrive.service import classify_skipped_file  # noqa: E402
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus  # noqa: E402

_STATUS = "NEEDS_MANUAL_CLASSIFICATION"


def _matches(session, batch_ids: list[UUID] | None) -> list[tuple[str, UUID, UUID, str]]:
    """(batch name, batch id, submission id, filename) for every candidate."""
    stmt = (
        select(Batch.name, Batch.id, OneDriveSubmission)
        .join(OneDriveSubmission, OneDriveSubmission.batch_id == Batch.id)
        .where(Batch.name.ilike("%nikah%"))
        .order_by(Batch.created_at, OneDriveSubmission.created_at)
    )
    if batch_ids:
        stmt = stmt.where(Batch.id.in_(batch_ids))
    rows: list[tuple[str, UUID, UUID, str]] = []
    for name, batch_id, submission in session.execute(stmt):
        if submission.status != OneDriveSubmissionStatus.FETCHED.value:
            continue
        for item in submission.skipped_files or []:
            if item.get("status") == _STATUS and not item.get("refetch_status"):
                rows.append((name, batch_id, submission.id, item["filename"]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, action="append", help="limit to this batch (repeatable)")
    parser.add_argument("--limit", type=int, default=20, help="max files per run (default 20; 0 = no limit)")
    parser.add_argument("--execute", action="store_true", help="actually classify")
    args = parser.parse_args()

    settings = get_settings()
    session_factory = get_session_factory(settings)
    with session_factory() as session:
        matches = _matches(session, args.batch_id)
    selected = matches[: args.limit] if args.limit else matches
    print(
        f"{len(matches)} NEEDS_MANUAL_CLASSIFICATION file(s) in Nikah batches; {len(selected)} selected"
        + ("" if args.execute else "  [DRY RUN -- pass --execute to act]")
    )
    per_batch: dict[tuple[str, UUID], int] = {}
    for name, batch_id, _, _ in matches:
        per_batch[(name, batch_id)] = per_batch.get((name, batch_id), 0) + 1
    for (name, batch_id), count in per_batch.items():
        print(f"  {count:5d}  {batch_id}  {name}")
    if not args.execute:
        return

    from marriage_ocr_api.jobs.celery_executor import CeleryJobExecutor
    from marriage_ocr_api.onedrive.celery_executor import CeleryOneDriveExecutor

    job_executor, onedrive_executor = CeleryJobExecutor(), CeleryOneDriveExecutor()
    done = failed = 0
    for _, _, submission_id, filename in selected:
        with session_factory() as session:
            try:
                classify_skipped_file(
                    session,
                    settings,
                    job_executor,
                    submission_id,
                    filename=filename,
                    document_type=DocumentType.HANDWRITTEN_REGISTER,
                    onedrive_executor=onedrive_executor,
                )
            except ApiError as exc:
                session.rollback()
                failed += 1
                print(f"  SKIP {submission_id}: {exc.code}")
                continue
        done += 1
    print(f"done: {done} classified, {failed} skipped")


if __name__ == "__main__":
    main()
