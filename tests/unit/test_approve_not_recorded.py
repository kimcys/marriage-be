from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "approve_not_recorded.py"
_spec = importlib.util.spec_from_file_location("approve_not_recorded", _SCRIPT)
approve = importlib.util.module_from_spec(_spec)
sys.modules["approve_not_recorded"] = approve
_spec.loader.exec_module(approve)

NR = "TIADA MAKLUMAT"


def _fake_normalize(text: str) -> str | None:
    return "RM 80.00" if text == "80.00" else None


def _plan(field_values, issues):
    return approve.plan_update(field_values, issues, {"raw": {}}, normalize=_fake_normalize)


def test_issues_explained_by_not_recorded_fields_are_approved_and_audited() -> None:
    fields = {"Umur Suami": NR, "Umur Isteri": NR, "Mas Kahwin": NR, "Nama Suami": "A"}
    issues = ["invalid husband age", "invalid wife age", "missing mas kahwin"]
    plan = _plan(fields, issues)
    assert plan["status"] == plan["review_status"] == "APPROVED"
    assert plan["validation_issues"] == []
    assert plan["normalized_data"]["auto_approved_not_recorded"] == issues
    assert plan["normalized_data"]["raw"] == {}


def test_a_field_that_has_a_value_is_not_treated_as_not_recorded() -> None:
    assert _plan({"Umur Suami": "999"}, ["invalid husband age"]) is None
    assert _plan({"IC Baru Suami": NR, "IC Lama Suami": "123"}, ["missing or invalid husband IC"]) is None


def test_any_other_issue_keeps_the_record_in_review() -> None:
    fields = {"Umur Suami": NR}
    assert _plan(fields, ["invalid husband age", "invalid nikah date"]) is None
    assert _plan(fields, ["Gemini uncertain fields: nama_suami"]) is None
    assert _plan(fields, []) is None


def test_mas_kahwin_missing_rm_prefix_is_normalized_then_approved() -> None:
    plan = _plan({"Mas Kahwin": "80.00", "Umur Suami": NR}, ["mas kahwin missing RM prefix", "invalid husband age"])
    assert plan["field_values"]["Mas Kahwin"] == "RM 80.00"
    assert plan["status"] == "APPROVED"
    assert _plan({"Mas Kahwin": "garbage"}, ["mas kahwin missing RM prefix"]) is None
