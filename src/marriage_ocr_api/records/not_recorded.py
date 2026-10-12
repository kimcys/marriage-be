"""Auto-resolve handwritten-register issues that exist only because the book never had the data.

The older register books often record no age, mas kahwin, registrar, IC, wali or saksi, and some
no nikah date. The reader writes "TIADA MAKLUMAT" (no information) for those and the validators
flag each one ("invalid husband age", "missing mas kahwin", ...). No reviewer can complete a
field the book does not have, so a record whose every issue is explained that way is approved
without review. Also handled on the way: a Mas Kahwin the validator rejected only for lacking
"RM" is normalized, and an IC of 12 digits with odd punctuation ("740304 - 14 - 5297") is
reformatted to YYMMDD-PB-####. Any other issue (an unreadable date, an IC with letters or the
wrong digit count, "Gemini uncertain" fields, a cross-reference note, ...) leaves the record in
review exactly as before.

What was cleared is kept in normalized_data so the decision can be audited or undone:
"auto_approved_not_recorded" (the issues) and "auto_approved_original_values" (changed values).
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

NOT_RECORDED = frozenset({"", "TIADA MAKLUMAT", "TIADA", "-"})
RM_PREFIX_ISSUE = "mas kahwin missing RM prefix"
# issue text -> the fields that must ALL be not-recorded for the issue to be "explained"
EXPLAINED_BY: dict[str, list[str]] = {
    "invalid husband age": ["Umur Suami"],
    "invalid wife age": ["Umur Isteri"],
    "missing mas kahwin": ["Mas Kahwin"],
    "missing pendaftar name": ["Nama Pendaftar"],
    "missing pendaftar address": ["Alamat Pendaftar"],
    "invalid nikah date": ["Tarikh Nikah"],
    "invalid keluar date": ["Tarikh Keluar"],
    "missing wali name": ["Nama Wali"],
    "missing wali relationship": ["Hubungan Wali"],
    "missing saksi 1": ["Saksi 1"],
    "missing saksi 2": ["Saksi 2"],
}
# IC issues: explained when each field is not recorded or a 12-digit IC once reformatted
IC_ISSUES: dict[str, list[str]] = {
    "missing or invalid husband IC": ["IC Baru Suami", "IC Lama Suami"],
    "missing or invalid wife IC": ["IC Baru Isteri", "IC Lama Isteri"],
    "missing both IC values": ["IC Baru Suami", "IC Lama Suami", "IC Baru Isteri", "IC Lama Isteri"],
}


@dataclass(frozen=True)
class Resolution:
    field_values: dict[str, object]
    normalized_data: dict[str, object]


def _not_recorded(value: object) -> bool:
    return str(value or "").strip().upper() in NOT_RECORDED


def format_ic(value: object) -> str | None:
    """YYMMDD-PB-#### when the value is 12 digits plus punctuation/spaces (no letters), else None."""
    text = str(value or "")
    digits = re.sub(r"\D", "", text)
    if len(digits) != 12 or re.search(r"[A-Za-z]", text):
        return None
    return f"{digits[:6]}-{digits[6:8]}-{digits[8:]}"


def _default_amount_normalizer() -> Callable[[str], str | None] | None:
    try:
        module = importlib.import_module("marriage_ocr.typed.normalizer")
    except ImportError:  # marriage-ocr is installed in the app image; unit tests may not have it
        return None
    return cast("Callable[[str], str | None]", module.normalize_mas_kahwin)


def resolve(
    field_values: dict[str, object] | None,
    validation_issues: list[str] | None,
    normalized_data: dict[str, object] | None = None,
    *,
    normalize_amount: Callable[[str], str | None] | None = None,
) -> Resolution | None:
    """The cleaned values when every issue is explained by a not-recorded field, else None."""
    issues = list(validation_issues or [])
    if not issues:
        return None
    values = dict(field_values or {})
    original_values: dict[str, object] = {}
    for issue in issues:
        if issue == RM_PREFIX_ISSUE:
            normalize = normalize_amount or _default_amount_normalizer()
            amount = normalize(str(values.get("Mas Kahwin") or "")) if normalize else None
            if not amount:
                return None
            original_values.setdefault("Mas Kahwin", values.get("Mas Kahwin"))
            values["Mas Kahwin"] = amount
        elif issue in IC_ISSUES:
            for field in IC_ISSUES[issue]:
                if _not_recorded(values.get(field)):
                    continue
                formatted = format_ic(values.get(field))
                if formatted is None:
                    return None
                if values[field] != formatted:
                    original_values.setdefault(field, values[field])
                    values[field] = formatted
        elif issue in EXPLAINED_BY:
            if not all(_not_recorded(values.get(field)) for field in EXPLAINED_BY[issue]):
                return None
        else:
            return None
    audit: dict[str, object] = {**(normalized_data or {}), "auto_approved_not_recorded": issues}
    if original_values:
        audit["auto_approved_original_values"] = original_values
    return Resolution(field_values=values, normalized_data=audit)
