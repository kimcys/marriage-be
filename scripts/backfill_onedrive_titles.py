"""Fill in the folder name (title) of OneDrive submissions that have none.

Resolves each link's name from its redirect -- nothing is downloaded and no
worker slot or OCR/Gemini call is used. Submissions that already have a title
are left alone; ones whose name can't be resolved are reported and left null.

DRY RUN BY DEFAULT: prints what it would set. Pass --execute to apply.

Needs the vendored marriage-ocr checkout, so run it in the worker container
(scripts/ is not copied into the image, so pipe it in):
  docker compose --env-file .env.production exec -T worker \
      python - [--batch-id <uuid>] [--execute] < scripts/backfill_onedrive_titles.py
"""

from __future__ import annotations

import argparse
from uuid import UUID

from sqlalchemy import select

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.onedrive.models import OneDriveSubmission
from marriage_ocr_api.onedrive.runner import OneDriveFetchRunner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", type=UUID, default=None, help="only this batch (default: all)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    runner = OneDriveFetchRunner(settings)
    with get_session_factory(settings)() as session:
        query = select(OneDriveSubmission).where(OneDriveSubmission.title.is_(None))
        if args.batch_id is not None:
            query = query.where(OneDriveSubmission.batch_id == args.batch_id)
        submissions = list(session.scalars(query.order_by(OneDriveSubmission.created_at)))
        print(f"{len(submissions)} submission(s) without a title")
        resolved = 0
        for submission in submissions:
            try:
                title = runner.resolve_title(submission.url)
            except Exception as exc:  # noqa: BLE001 - report and keep going
                print(f"  FAIL  {submission.id}  {submission.url}  ({exc})")
                continue
            if not title:
                print(f"  NONE  {submission.id}  {submission.url}")
                continue
            print(f"  {'SET ' if args.execute else 'WOULD'}  {submission.id}  {title}")
            resolved += 1
            if args.execute:
                submission.title = title[:512]
                session.commit()
        print(f"{resolved} resolved" + ("" if args.execute else " (dry run; pass --execute to apply)"))


if __name__ == "__main__":
    main()
