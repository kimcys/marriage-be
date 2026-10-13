from __future__ import annotations

from marriage_ocr_api.records import not_recorded

NR = "TIADA MAKLUMAT"


def _fake_normalize(text: str) -> str | None:
    return "RM 80.00" if text == "80.00" else None


def _plan(field_values, issues):
    resolution = not_recorded.resolve(field_values, issues, {"raw": {}}, normalize_amount=_fake_normalize)
    if resolution is None:
        return None
    return {
        "field_values": resolution.field_values,
        "normalized_data": resolution.normalized_data,
        "status": "APPROVED",
        "validation_issues": [],
    }


def test_issues_explained_by_not_recorded_fields_are_approved_and_audited() -> None:
    fields = {"Umur Suami": NR, "Umur Isteri": NR, "Mas Kahwin": NR, "Nama Suami": "A"}
    issues = ["invalid husband age", "invalid wife age", "missing mas kahwin"]
    plan = _plan(fields, issues)
    assert plan["status"] == "APPROVED"
    assert plan["validation_issues"] == []
    assert plan["normalized_data"]["auto_approved_not_recorded"] == issues
    assert plan["normalized_data"]["raw"] == {}


def test_a_field_that_has_a_value_is_not_treated_as_not_recorded() -> None:
    assert _plan({"Umur Suami": "999"}, ["invalid husband age"]) is None
    assert _plan({"IC Baru Suami": NR, "IC Lama Suami": "123"}, ["missing or invalid husband IC"]) is None


def test_any_other_issue_keeps_the_record_in_review() -> None:
    fields = {"Umur Suami": NR}
    assert _plan(fields, ["invalid husband age", "cross-reference note to another register book"]) is None
    assert _plan(fields, ["Gemini uncertain fields: nama_suami"]) is None
    assert _plan(fields, []) is None


def test_mas_kahwin_missing_rm_prefix_is_normalized_then_approved() -> None:
    plan = _plan({"Mas Kahwin": "80.00", "Umur Suami": NR}, ["mas kahwin missing RM prefix", "invalid husband age"])
    assert plan["field_values"]["Mas Kahwin"] == "RM 80.00"
    assert plan["status"] == "APPROVED"
    assert _plan({"Mas Kahwin": "garbage"}, ["mas kahwin missing RM prefix"]) is None


def test_ic_with_odd_punctuation_is_reformatted_and_the_original_kept() -> None:
    fields = {"IC Baru Suami": "740304 - 14 - 5297", "IC Lama Suami": NR}
    plan = _plan(fields, ["missing or invalid husband IC"])
    assert plan["field_values"]["IC Baru Suami"] == "740304-14-5297"
    assert plan["normalized_data"]["auto_approved_original_values"] == {"IC Baru Suami": "740304 - 14 - 5297"}
    assert plan["status"] == "APPROVED"


def test_ic_that_is_not_twelve_digits_stays_in_review() -> None:
    assert _plan({"IC Baru Suami": "A-1587198"}, ["missing or invalid husband IC"]) is None
    assert _plan({"IC Baru Suami": "84/208 - 14 - 5054"}, ["missing or invalid husband IC"]) is None
    assert (
        _plan({"IC Baru Suami": "740304-14-5297", "IC Lama Suami": "SPLP 603431"}, ["missing or invalid husband IC"])
        is None
    )


def test_missing_nikah_date_and_empty_keluar_date_count_as_not_recorded() -> None:
    plan = _plan({"Tarikh Nikah": NR, "Tarikh Keluar": ""}, ["invalid nikah date", "invalid keluar date"])
    assert plan["status"] == "APPROVED"
    assert _plan({"Tarikh Nikah": "1993-0?-08"}, ["invalid nikah date"]) is None


def test_a_cross_reference_note_alone_is_approved_with_values_unchanged() -> None:
    fields = {"Bil": "623/09", "Hal Hal Lain": "SILA RUJUK BUKU DAFTAR SALINAN CERAI"}
    plan = _plan(fields, [not_recorded.CROSS_REFERENCE_ISSUE])
    assert plan["status"] == "APPROVED"
    assert plan["field_values"] == fields
    assert plan["normalized_data"]["auto_approved_not_recorded"] == [not_recorded.CROSS_REFERENCE_ISSUE]


def test_a_cross_reference_note_with_another_problem_stays_in_review() -> None:
    assert _plan({"Nama Suami": "A"}, [not_recorded.CROSS_REFERENCE_ISSUE, "invalid or missing cerai date"]) is None


def test_a_row_with_no_data_whose_remarks_point_at_another_book_is_approved() -> None:
    fields = {"Bil": "7/2004", "Nama Suami": NR, "Nama Isteri": NR, "IC Suami": NR, "IC Isteri": NR, "Tarikh Cerai": NR}
    for note in ("RUJUK BUKU CERAI SALINAN YE", "SILA RUJUK BUKU SALINAN", "- Sama -"):
        plan = _plan({**fields, "Hal Hal Lain": note}, ["missing husband name", "missing wife name"])
        assert plan is not None and plan["status"] == "APPROVED"


def test_a_row_with_no_data_but_other_remarks_stays_in_review() -> None:
    fields = {"Bil": "7/2004", "Nama Suami": NR, "Nama Isteri": NR, "IC Suami": NR, "IC Isteri": NR, "Tarikh Cerai": NR}
    assert _plan({**fields, "Hal Hal Lain": "BATAL"}, ["missing husband name"]) is None
    # a row that does have a name is never treated as a pointer
    assert _plan({**fields, "Nama Suami": "A", "Hal Hal Lain": "RUJUK BUKU"}, ["missing wife name"]) is None


def test_a_numbered_row_nobody_filled_in_is_approved_as_a_blank_entry() -> None:
    blank = {
        "Bil": "125/08",
        "Record Type": "CERAI",
        "Nama Suami": NR,
        "Nama Isteri": NR,
        "IC Suami": NR,
        "Tarikh Cerai": NR,
    }
    plan = _plan(blank, ["missing husband name", "missing wife name"])
    assert plan is not None and plan["status"] == "APPROVED"
    assert plan["field_values"] == blank


def test_a_row_with_anything_besides_the_bil_is_not_a_blank_entry() -> None:
    blank = {"Bil": "125/08", "Nama Suami": NR, "Nama Isteri": NR}
    assert _plan({**blank, "Hal Hal Lain": "BATAL"}, ["missing husband name"]) is None
    assert _plan({**blank, "Tarikh Keluar": "6/3/2008"}, ["missing husband name"]) is None
    assert _plan({**blank, "Nama Suami": "A"}, ["missing wife name"]) is None
    assert _plan({"Nama Suami": NR}, ["missing husband name"]) is None  # no Bil at all: not a numbered entry


def test_a_row_with_only_a_phone_number_is_a_split_off_copy_and_is_approved() -> None:
    row = {"Bil": "784/13", "Nama Suami": NR, "Nama Isteri": NR, "IC Suami": NR, "Hal Hal Lain": "017-3369865"}
    for phone in ("017-3369865", "012 3071224.", "0102570065", "010-233-1129"):
        plan = _plan({**row, "Hal Hal Lain": phone}, ["missing husband name", "missing wife name"])
        assert plan is not None and plan["status"] == "APPROVED"
    # a phone number alongside any other real value, or text mixed in, still goes to review
    assert _plan({**row, "Tarikh Keluar": "6/3/2008"}, ["missing husband name"]) is None
    assert _plan({**row, "Hal Hal Lain": "017-3369865 BATAL"}, ["missing husband name"]) is None
