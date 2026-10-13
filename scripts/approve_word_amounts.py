"""Approve typed records whose Mas Kahwin was written as plain Malay number words.

The pipeline converts the words to digits ("Lapan Puluh" -> "RM 80") but keeps the record in
review with the note "mas kahwin was written in words (...), read as ...; confirm against the scan",
because the reader once read the same handwriting two ways. A sample of 9 scans (Lapan Puluh,
Empat Puluh, Seratus sembilan puluh) matched the stored amount every time, so the unambiguous ones
are approved here. DRY RUN BY DEFAULT; pass --execute to apply.

Approved only when: the record is untouched by a human (version 1, no reviewer); the words are all
ordinary number words (no "sepuluh", the word that was once mistaken for "lapan puluh"; no in-kind
text); the amount is a whole multiple of RM 10 between RM 20 and RM 300 (the range the 9-scan
sample covered); and nothing else is missing or flagged. The note removed is kept in
normalized_data["auto_approved_word_amount"].

  docker compose --env-file .env.production exec -T api python - [--execute] < scripts/approve_word_amounts.py
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
from collections import Counter
from pathlib import Path

with contextlib.suppress(NameError):  # no __file__ when piped to `python -`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

from marriage_ocr_api.batches.status import TYPED_DOCUMENT_TYPES  # noqa: E402
from marriage_ocr_api.db.models import OCRJob  # noqa: E402
from marriage_ocr_api.db.session import get_session_factory  # noqa: E402
from marriage_ocr_api.records.models import OCRRecord  # noqa: E402
from marriage_ocr_api.records.status import RecordStatus  # noqa: E402

NOTE = re.compile(
    r"mas kahwin was written in words \('(?P<raw>.*)'\), "
    r"read as (?P<value>RM [\d,]+(?:\.\d+)?); confirm against the scan$"
)
PLAIN_WORDS = frozenset(
    [
        "satu",
        "dua",
        "tiga",
        "empat",
        "lima",
        "enam",
        "tujuh",
        "lapan",
        "sembilan",
        "puluh",
        "belas",
        "ratus",
        "ribu",
        "seratus",
        "seribu",
        "ringgit",
        "sahaja",
        "malaysia",
        "rm",
        "dan",
    ]
)
FIELD = "Mas Kahwin"


def plan_update(field_values, missing_fields, validation_issues, normalized_data) -> dict | None:
    """The approved record's new columns, or None when it should stay in review."""
    issues = list(validation_issues or [])
    notes = [i for i in issues if "written in words" in str(i)]
    if len(notes) != 1 or [i for i in issues if i not in notes]:
        return None  # no note, or something else is flagged too
    if [f for f in (missing_fields or []) if f != FIELD]:
        return None  # another field is missing
    match = NOTE.match(str(notes[0]))
    if match is None:
        return None
    words = re.sub(r"[^a-z\s]", " ", match["raw"].lower()).split()
    if not words or any(w not in PLAIN_WORDS for w in words):
        return None  # includes "sepuluh" and in-kind text
    if str((field_values or {}).get(FIELD) or "").strip() != match["value"]:
        return None
    amount = float(match["value"].removeprefix("RM ").replace(",", ""))
    if amount < 20 or amount > 300 or amount % 10:
        return None  # only the common small amounts the sample covered
    return {
        "missing_fields": [],
        "validation_issues": [],
        "normalized_data": {**(normalized_data or {}), "auto_approved_word_amount": notes[0]},
        "status": RecordStatus.APPROVED.value,
        "review_status": RecordStatus.APPROVED.value,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    typed = [t.value if hasattr(t, "value") else str(t) for t in TYPED_DOCUMENT_TYPES]
    stmt = (
        select(OCRRecord)
        .join(OCRJob, OCRRecord.job_id == OCRJob.id)
        .where(
            OCRJob.document_type.in_(typed),
            OCRRecord.status == RecordStatus.PENDING_REVIEW.value,
            OCRRecord.version == 1,
            OCRRecord.reviewed_by.is_(None),
        )
    )
    approved = 0
    values: Counter[str] = Counter()
    with get_session_factory()() as session:
        for record in session.scalars(stmt):
            plan = plan_update(
                record.field_values, record.missing_fields, record.validation_issues, record.normalized_data
            )
            if plan is None:
                continue
            values[str(record.field_values.get(FIELD))] += 1
            approved += 1
            if args.execute:
                for column, value in plan.items():
                    setattr(record, column, value)
        if args.execute:
            session.commit()
    verdict = "approved" if args.execute else "would be approved  [DRY RUN -- pass --execute to apply]"
    print(f"{approved} record(s) {verdict}")
    print("by amount:", dict(values.most_common()))


if __name__ == "__main__":
    main()
