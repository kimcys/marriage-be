from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from marriage_ocr_api.exports.writer import write_csv_export, write_xlsx_export


def _sheet_values(xlsx_path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(xlsx_path) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")
    root = ET.fromstring(xml)
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        values: list[str] = []
        for cell in row.findall("a:c", ns):
            inline = cell.find("a:is/a:t", ns)
            if inline is not None and inline.text is not None:
                values.append(inline.text)
                continue
            value = cell.findtext("a:v", default="", namespaces=ns)
            values.append(value)
        rows.append(values)
    return rows


def test_csv_writer_emits_utf8_bom_and_stable_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "records.csv"
    write_csv_export(
        csv_path,
        rows=[
            {"full_name": "Ada Lovelace", "confidence": "0.97"},
            {"full_name": "Grace Hopper", "confidence": "0.95"},
        ],
        columns=["full_name", "confidence"],
    )

    payload = csv_path.read_bytes()
    assert payload.startswith(b"\xef\xbb\xbf")
    assert payload.decode("utf-8-sig").splitlines() == [
        "full_name,confidence",
        "Ada Lovelace,0.97",
        "Grace Hopper,0.95",
    ]


def test_xlsx_writer_preserves_column_order_and_cell_values(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "records.xlsx"
    write_xlsx_export(
        xlsx_path,
        rows=[
            {"full_name": "Ada Lovelace", "confidence": "0.97"},
            {"full_name": "Grace Hopper", "confidence": "0.95"},
        ],
        columns=["full_name", "confidence"],
    )

    assert _sheet_values(xlsx_path) == [
        ["full_name", "confidence"],
        ["Ada Lovelace", "0.97"],
        ["Grace Hopper", "0.95"],
    ]
