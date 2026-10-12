from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_mas_kahwin_from_raw.py"
_spec = importlib.util.spec_from_file_location("backfill_mas_kahwin_from_raw", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
sys.modules["backfill_mas_kahwin_from_raw"] = backfill
_spec.loader.exec_module(backfill)


def _normalize(raw: str) -> str | None:  # stand-in for marriage_ocr's normalize_mas_kahwin_value
    return {
        "300.00 (TUNAI)": "RM 300.00",
        "1 DINAR EMAS": "1 DINAR EMAS",
        "lapan puluh": "RM 80",
        "RM...": "RM...",
    }.get(raw)


def _plan(raw, *, missing=("Mas Kahwin",), issues=("review required",), current=None):
    field_values = {"Bil": "1/13", **({"Mas Kahwin": current} if current else {})}
    return backfill.plan_update(
        field_values, {"raw": {"Mas Kahwin": raw}}, list(missing), list(issues), normalize=_normalize
    )


def test_amount_without_rm_is_filled_and_approved() -> None:
    plan = _plan("300.00 (TUNAI)")
    assert plan["field_values"]["Mas Kahwin"] == "RM 300.00"
    assert plan["missing_fields"] == []
    assert plan["validation_issues"] == []
    assert plan["status"] == plan["review_status"] == "APPROVED"


def test_in_kind_value_is_kept_as_written_and_approved() -> None:
    plan = _plan("1 DINAR EMAS")
    assert plan["field_values"]["Mas Kahwin"] == "1 DINAR EMAS"
    assert plan["status"] == "APPROVED"


def test_other_missing_field_keeps_the_record_in_review() -> None:
    plan = _plan("300.00 (TUNAI)", missing=("Mas Kahwin", "IC Isteri"))
    assert plan["field_values"]["Mas Kahwin"] == "RM 300.00"
    assert plan["missing_fields"] == ["IC Isteri"]
    assert plan["status"] == "PENDING_REVIEW"


def test_amount_written_in_words_stays_in_review_with_a_note() -> None:
    plan = _plan("lapan puluh")
    assert plan["field_values"]["Mas Kahwin"] == "RM 80"
    assert plan["status"] == "PENDING_REVIEW"
    assert "Mas Kahwin" in plan["missing_fields"]
    assert any("written in words" in issue for issue in plan["validation_issues"])


def test_records_with_a_value_or_nothing_readable_are_left_alone() -> None:
    assert _plan("300.00 (TUNAI)", current="RM 80.00") is None
    assert _plan("unreadable") is None
    assert _plan("") is None
    assert backfill.plan_update({"Bil": "1"}, None, [], [], normalize=_normalize) is None


def test_junk_starting_with_rm_is_left_for_a_person() -> None:
    assert _plan("RM...") is None
