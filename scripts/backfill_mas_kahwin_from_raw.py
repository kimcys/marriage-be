"""Fill in Mas Kahwin on typed records from the reader's own stored text.

marriage-ocr used to drop a Mas Kahwin it could not read as "RM <amount>" --
"300.00 (TUNAI)" without the RM, or a value in kind or another currency ("1
DINAR EMAS", "20 POUND STERLING"). The text the reader returned is stored in
normalized_data["raw"]["Mas Kahwin"], so no new OCR/Gemini call is needed: this
runs the current normalizer (marriage_ocr.typed.normalizer.normalize_mas_kahwin_value)
over that stored text.

DRY RUN BY DEFAULT: prints what it would change. Pass --execute to apply.

Only records a human has not touched (version 1, no reviewer) whose Mas Kahwin is
empty and whose stored raw text now normalizes to a value are changed. For each:
  * field_values["Mas Kahwin"] is set and "Mas Kahwin" leaves missing_fields,
  * if nothing else is missing and the only issue left is the generic
    "review required", the record becomes APPROVED (issues cleared); otherwise it
    stays PENDING_REVIEW,
  * an amount the reader wrote in words (no digits, converted to "RM ...") is filled
    in but stays in review with a note, as the importer does.

scripts/ is not copied into the image, so pipe it into the api container:
  docker compose --env-file .env.production exec -T api \
      python - --created-on 2026-10-03 < scripts/backfill_mas_kahwin_from_raw.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

with contextlib.suppress(NameError):  # no __file__ when piped to `python -` (package is installed in the container)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from marriage_ocr_api.batches.status import TYPED_DOCUMENT_TYPES  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.records.models import OCRRecord  # noqa: E402
from marriage_ocr_api.records.status import RecordStatus  # noqa: E402

FIELD = "Mas Kahwin"
GENERIC_ISSUE = "review required"


def plan_update(
    field_values: dict | None,
    normalized_data: dict | None,
    missing_fields: list[str] | None,
    validation_issues: list[str] | None,
    normalize=None,
) -> dict | None:
    """The new column values for a record, or None when it should be left alone."""
    if normalize is None:  # imported here so the function can be unit-tested without marriage-ocr installed
        from marriage_ocr.typed.normalizer import normalize_mas_kahwin_value as normalize
    field_values = dict(field_values or {})
    flagged = FIELD in (missing_fields or [])
    if str(field_values.get(FIELD) or "").strip() and not flagged:
        return None  # has a value nothing complained about
    raw_block = (normalized_data or {}).get("raw")
    raw = str(raw_block.get(FIELD) or "").strip() if isinstance(raw_block, dict) else ""
    if not raw:
        return None
    value = normalize(raw)
    if not value and flagged and str(field_values.get(FIELD) or "").strip():
        # A value an earlier, looser reading put there that the current rules refuse ("Sepuluh Puluh"
        # read as RM 20, a bare "RM"): clear it so a wrong amount is not left standing, keep it in review.
        issues = [i for i in (validation_issues or []) if "written in words" not in str(i)]
        issues.append(f"mas kahwin {raw!r} could not be read as an amount; confirm against the scan")
        return {
            "field_values": {**field_values, FIELD: ""},
            "missing_fields": list(missing_fields or []),
            "validation_issues": issues,
            "status": RecordStatus.PENDING_REVIEW.value,
            "review_status": RecordStatus.PENDING_REVIEW.value,
        }
    if not value or (value.startswith("RM") and not value.startswith("RM ")):
        return None  # junk such as "RM...", "RM-80/=": leave it for a person
    field_values[FIELD] = value
    missing = [field for field in (missing_fields or []) if field != FIELD]
    issues = list(validation_issues or [])
    if value.startswith("RM") and not any(char.isdigit() for char in raw):
        note = f"mas kahwin was written in words ({raw!r}), read as {value}; confirm against the scan"
        if note not in issues:
            issues.append(note)
        if FIELD not in missing:
            missing.append(FIELD)
    elif not missing and set(issues) <= {GENERIC_ISSUE}:
        issues = []
    issues = list(dict.fromkeys(issues))  # never the same note twice
    status = RecordStatus.PENDING_REVIEW if (missing or issues) else RecordStatus.APPROVED
    return {
        "field_values": field_values,
        "missing_fields": missing,
        "validation_issues": issues,
        "status": status.value,
        "review_status": status.value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--created-on", type=date.fromisoformat, help="only jobs created that UTC day")
    parser.add_argument("--limit", type=int, default=0, help="max records (0 = no limit)")
    parser.add_argument("--execute", action="store_true", help="apply the changes")
    args = parser.parse_args()

    typed = [t.value if hasattr(t, "value") else str(t) for t in TYPED_DOCUMENT_TYPES]
    stmt = (
        select(OCRRecord)
        .join(OCRJob, OCRRecord.job_id == OCRJob.id)
        .where(OCRJob.document_type.in_(typed), OCRRecord.version == 1, OCRRecord.reviewed_by.is_(None))
        .order_by(OCRRecord.id)
    )
    if args.created_on:
        start = datetime.combine(args.created_on, datetime.min.time(), tzinfo=UTC)
        stmt = stmt.where(OCRJob.created_at >= start, OCRJob.created_at < start + timedelta(days=1))

    outcomes: Counter[str] = Counter()
    examples: dict[str, str] = {}
    changed = 0
    with get_session_factory()() as session:
        for record in session.scalars(stmt):
            plan = plan_update(
                record.field_values, record.normalized_data, record.missing_fields, record.validation_issues
            )
            if plan is None:
                continue
            outcomes[f"{plan['status']}"] += 1
            examples.setdefault(plan["field_values"][FIELD], str(record.id))
            if args.execute:
                for column, new_value in plan.items():
                    setattr(record, column, new_value)
            changed += 1
            if args.limit and changed >= args.limit:
                break
            if args.execute and changed % 500 == 0:
                session.commit()
        if args.execute:
            session.commit()
    print(
        f"{changed} record(s) {'updated' if args.execute else 'would be updated  [DRY RUN -- pass --execute to apply]'}"
    )
    print("resulting status:", dict(outcomes))
    for value, record_id in list(examples.items())[:15]:
        print(f"  e.g. {value!r}  record {record_id}")


if __name__ == "__main__":
    main()
