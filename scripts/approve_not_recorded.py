"""Approve handwritten-register records whose only problems are fields the register never had.

The older register books often record no age, mas kahwin, registrar, IC, wali or saksi.
The reader writes "TIADA MAKLUMAT" (no information) for those and the validators then
flag each one ("invalid husband age", "missing mas kahwin", ...). Such a field cannot be
completed by anyone, so a record whose every issue is explained by a not-recorded field
is approved here. New uploads get the same treatment automatically at import time
(records/not_recorded.py, setting AUTO_APPROVE_NOT_RECORDED); this script is for records
imported before that, or after switching the setting off. A Mas Kahwin the validator rejected
only for lacking "RM" is first normalized ("80.00" -> "RM 80.00"), and an IC of 12 digits with
odd punctuation or spacing ("740304 - 14 - 5297") is reformatted to YYMMDD-PB-#### ("740304-14-5297"). A nikah date
the book does not have ("TIADA MAKLUMAT") and an empty keluar date count as not recorded.

DRY RUN BY DEFAULT. Pass --execute to apply. Only handwritten records (register, divorce, rujuk) still
PENDING_REVIEW and untouched by a human (version 1, no reviewer) are considered. A record
with any other issue (invalid date, an IC that is present but invalid, "Gemini uncertain"
fields, a cross-reference note, ...) is left in review. The issues cleared are kept in
normalized_data["auto_approved_not_recorded"] so the decision can be audited or undone.

  docker compose --env-file .env.production exec -T api python - < scripts/approve_not_recorded.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections import Counter
from pathlib import Path

with contextlib.suppress(NameError):  # no __file__ when piped to `python -`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.records import not_recorded  # noqa: E402
from marriage_ocr_api.records.models import OCRRecord  # noqa: E402
from marriage_ocr_api.records.status import RecordStatus  # noqa: E402


def plan_update(
    field_values: dict | None, validation_issues: list[str] | None, normalized_data: dict | None, normalize=None
) -> dict | None:
    """New column values when every issue is explained by a not-recorded field, else None."""
    resolution = not_recorded.resolve(field_values, validation_issues, normalized_data, normalize_amount=normalize)
    if resolution is None:
        return None
    return {
        "field_values": resolution.field_values,
        "validation_issues": [],
        "normalized_data": resolution.normalized_data,
        "status": RecordStatus.APPROVED.value,
        "review_status": RecordStatus.APPROVED.value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="apply the changes")
    parser.add_argument("--limit", type=int, default=0, help="max records (0 = no limit)")
    args = parser.parse_args()

    stmt = (
        select(OCRRecord)
        .join(OCRJob, OCRRecord.job_id == OCRJob.id)
        .where(
            OCRJob.document_type.like("HANDWRITTEN_%"),
            OCRRecord.status == RecordStatus.PENDING_REVIEW.value,
            OCRRecord.version == 1,
            OCRRecord.reviewed_by.is_(None),
        )
        .order_by(OCRRecord.id)
    )
    changed = 0
    prefix_fixed = 0
    by_conf: Counter[str] = Counter()
    with get_session_factory()() as session:
        for record in session.scalars(stmt):
            plan = plan_update(record.field_values, record.validation_issues, record.normalized_data)
            if plan is None:
                continue
            if not_recorded.RM_PREFIX_ISSUE in (record.validation_issues or []):
                prefix_fixed += 1
            conf = record.confidence
            by_conf[
                "high >=0.70" if (conf or 0) >= 0.70 else ("mid 0.5-0.7" if (conf or 0) >= 0.5 else "low <0.5")
            ] += 1
            if args.execute:
                for column, value in plan.items():
                    setattr(record, column, value)
            changed += 1
            if args.limit and changed >= args.limit:
                break
            if args.execute and changed % 500 == 0:
                session.commit()
        if args.execute:
            session.commit()
    verdict = "approved" if args.execute else "would be approved  [DRY RUN -- pass --execute to apply]"
    print(f"{changed} record(s) {verdict}")
    print("of which Mas Kahwin got an RM prefix:", prefix_fixed, "| by confidence:", dict(by_conf))


if __name__ == "__main__":
    main()
