from __future__ import annotations

from marriage_ocr_api.batches.status import DocumentType

# Inverse of marriage-ocr's own batch_runner.ROUTING_TABLE (keyed by
# (doc_type, record_type, layout_variant), as reported by `marriage-ocr
# classify`) -- maps a routable classification onto the DocumentType this
# API stores. HANDWRITTEN_REGISTER/TYPED_BORANG_4B (Nikah) are kept as the
# existing values rather than introducing NIKAY-suffixed ones, matching
# batches/status.py's own comment about not renaming them.
CLASSIFICATION_TO_DOCUMENT_TYPE: dict[tuple[str, str, str | None], DocumentType] = {
    ("handwritten", "nikah", "legacy"): DocumentType.HANDWRITTEN_REGISTER,
    ("handwritten", "cerai", "legacy"): DocumentType.HANDWRITTEN_CERAI_LEGACY,
    ("handwritten", "cerai", "modern"): DocumentType.HANDWRITTEN_CERAI_MODERN,
    ("handwritten", "rujuk", "legacy"): DocumentType.HANDWRITTEN_RUJUK_LEGACY,
    ("handwritten", "rujuk", "modern"): DocumentType.HANDWRITTEN_RUJUK_MODERN,
    ("typed", "nikah", None): DocumentType.TYPED_BORANG_4B,
    ("typed", "cerai", "legacy"): DocumentType.TYPED_CERAI_LEGACY,
    ("typed", "cerai", "modern"): DocumentType.TYPED_CERAI_MODERN,
    ("typed", "rujuk", "legacy"): DocumentType.TYPED_RUJUK_LEGACY,
    ("typed", "rujuk", "modern"): DocumentType.TYPED_RUJUK_MODERN,
}

# Mirrors marriage-ocr's own batch_runner.SUPPORTED_EXTENSIONS. Kept as a
# literal copy (not imported) since marriage-be only ever talks to
# marriage-ocr as a vendored subprocess, never as an importable dependency.
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff"}
