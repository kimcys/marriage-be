from __future__ import annotations

from pathlib import Path
from typing import cast

import pymupdf as fitz


def pdf_page_count(path: Path) -> int:
    with fitz.open(path) as document:
        return cast(int, document.page_count)


def split_pdf_into_pages(path: Path, output_dir: Path) -> list[Path]:
    """Split a multi-page PDF into one single-page PDF file per page.

    Each output file is a fully self-contained single-page PDF, so the OCR CLI
    (which only ever processes a whole file as one unit) can treat it as its
    own independently-retryable job with no changes on the marriage-ocr side.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    with fitz.open(path) as source:
        for page_index in range(source.page_count):
            page_document = fitz.open()
            try:
                page_document.insert_pdf(source, from_page=page_index, to_page=page_index)
                page_path = output_dir / f"page-{page_index + 1}.pdf"
                page_document.save(page_path)
                output_paths.append(page_path)
            finally:
                page_document.close()
    return output_paths
