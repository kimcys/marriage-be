from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _sanitize_formula_value(value: object) -> object:
    """Neutralize CSV/Excel formula injection by escaping leading trigger characters.

    Values sourced from OCR output or reviewer corrections are attacker-influenced
    (e.g. text extracted from an uploaded document). Without this guard, a value
    such as "=HYPERLINK(...)" would be interpreted as a live formula by Excel or
    Sheets when the export is opened.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def write_csv_export(path: Path, *, rows: list[dict[str, object]], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _sanitize_formula_value(row.get(column, "")) for column in columns})
    return path


def _column_letter(index: int) -> str:
    result = ""
    value = index
    while value >= 0:
        value, remainder = divmod(value, 26)
        result = chr(ord("A") + remainder) + result
        value -= 1
    return result


def _inline_cell(reference: str, value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_TRIGGER_CHARS):
        text = "'" + text
    if text == "":
        return f'<c r="{reference}" t="inlineStr"><is><t></t></is></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _sheet_xml(columns: list[str], rows: list[dict[str, object]]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    lines.append(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    )
    lines.append("<sheetData>")
    header_cells = []
    for index, column in enumerate(columns):
        header_cells.append(_inline_cell(f"{_column_letter(index)}1", column))
    lines.append(f'<row r="1">{"".join(header_cells)}</row>')
    for row_number, row in enumerate(rows, start=2):
        cells = []
        for index, column in enumerate(columns):
            value = row.get(column, "")
            reference = f"{_column_letter(index)}{row_number}"
            if isinstance(value, bool):
                cells.append(f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                cells.append(_inline_cell(reference, value))
        lines.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def write_xlsx_export(path: Path, *, rows: list[dict[str, object]], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                    (
                        '  <Default Extension="rels" '
                        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    ),
                    '  <Default Extension="xml" ContentType="application/xml"/>',
                    (
                        '  <Override PartName="/xl/workbook.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                    ),
                    (
                        '  <Override PartName="/xl/worksheets/sheet1.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    ),
                    (
                        '  <Override PartName="/xl/styles.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                    ),
                    "</Types>",
                ]
            ),
        )
        archive.writestr(
            "_rels/.rels",
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                    (
                        '  <Relationship Id="rId1" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                        'Target="xl/workbook.xml"/>'
                    ),
                    "</Relationships>",
                ]
            ),
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Records" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                    (
                        '  <Relationship Id="rId1" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                        'Target="worksheets/sheet1.xml"/>'
                    ),
                    (
                        '  <Relationship Id="rId2" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                        'Target="styles.xml"/>'
                    ),
                    "</Relationships>",
                ]
            ),
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>
""",
        )
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(columns, rows))
    return path
