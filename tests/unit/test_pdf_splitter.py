from __future__ import annotations

from pathlib import Path

import pymupdf

from marriage_ocr_api.batches.pdf_splitter import pdf_page_count, split_pdf_into_pages


def _make_pdf(path: Path, page_count: int) -> None:
    document = pymupdf.open()
    for index in range(page_count):
        page = document.new_page()
        page.insert_text((72, 72), f"page {index + 1}")
    document.save(path)
    document.close()


def test_pdf_page_count(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _make_pdf(pdf_path, 3)

    assert pdf_page_count(pdf_path) == 3


def test_split_pdf_into_pages_produces_one_file_per_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _make_pdf(pdf_path, 3)
    output_dir = tmp_path / "pages"

    pages = split_pdf_into_pages(pdf_path, output_dir)

    assert len(pages) == 3
    assert [page.name for page in pages] == ["page-1.pdf", "page-2.pdf", "page-3.pdf"]
    for index, page_path in enumerate(pages, start=1):
        assert page_path.is_file()
        assert pdf_page_count(page_path) == 1
        with pymupdf.open(page_path) as page_document:
            text = page_document[0].get_text()
        assert f"page {index}" in text


def test_split_pdf_into_pages_single_page_still_produces_one_file(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _make_pdf(pdf_path, 1)
    output_dir = tmp_path / "pages"

    pages = split_pdf_into_pages(pdf_path, output_dir)

    assert len(pages) == 1
    assert pdf_page_count(pages[0]) == 1
