from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "approve_word_amounts.py"
_spec = importlib.util.spec_from_file_location("approve_word_amounts", _SCRIPT)
approve = importlib.util.module_from_spec(_spec)
sys.modules["approve_word_amounts"] = approve
_spec.loader.exec_module(approve)


def _note(raw: str, value: str) -> str:
    return f"mas kahwin was written in words ({raw!r}), read as {value}; confirm against the scan"


def _plan(raw: str, value: str, *, missing=("Mas Kahwin",), extra_issues=()):
    return approve.plan_update(
        {"Mas Kahwin": value}, list(missing), [_note(raw, value), *extra_issues], {"raw": {"Mas Kahwin": raw}}
    )


def test_plain_number_words_are_approved_and_the_note_is_kept() -> None:
    plan = _plan("Lapan Puluh", "RM 80")
    assert plan["status"] == "APPROVED" and plan["validation_issues"] == [] and plan["missing_fields"] == []
    assert "Lapan Puluh" in plan["normalized_data"]["auto_approved_word_amount"]
    assert plan["normalized_data"]["raw"] == {"Mas Kahwin": "Lapan Puluh"}
    assert _plan("Seratus sembilan puluh", "RM 190") is not None
    assert _plan("TIGA RATUS RINGGIT MALAYSIA", "RM 300") is not None


def test_sepuluh_in_kind_text_and_odd_amounts_stay_in_review() -> None:
    assert _plan("Sepuluh", "RM 10") is None
    assert _plan("RM. SEUTAS RANTAI LEHER", "RM. SEUTAS RANTAI LEHER") is None
    assert _plan("lapan puluh lima", "RM 85") is None
    assert _plan("sebelas", "RM 11") is None
    assert _plan("tujuh ratus dua puluh", "RM 720") is None
    assert _plan("satu ribu", "RM 1,000") is None


def test_another_missing_field_or_issue_keeps_it_in_review() -> None:
    assert _plan("Lapan Puluh", "RM 80", missing=("Mas Kahwin", "IC Isteri")) is None
    assert _plan("Lapan Puluh", "RM 80", extra_issues=("review required",)) is None
    assert approve.plan_update({"Mas Kahwin": "RM 80"}, [], [], None) is None
    assert approve.plan_update({"Mas Kahwin": "RM 90"}, ["Mas Kahwin"], [_note("Lapan Puluh", "RM 80")], None) is None
