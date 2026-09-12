from __future__ import annotations

from marriage_ocr_api.batches.status import DocumentType

# Inverse of marriage-ocr's own batch_runner.ROUTING_TABLE (keyed by
# (doc_type, record_type, layout_variant), as reported by `marriage-ocr
# classify`) -- maps a routable classification onto the DocumentType this
# API stores. HANDWRITTEN_REGISTER (Nikah) is kept as the existing value
# rather than introducing a NIKAH-suffixed one, matching batches/status.py's
# own comment about not renaming it.
#
# Typed Nikah used to be a single ("typed", "nikah", None) entry mapping to
# DocumentType.TYPED_BORANG_4B -- marriage-ocr's `classify` never reported a
# layout_variant for it. marriage-ocr has since split typed Nikah into
# legacy/modern the same way Cerai/Rujuk already were, so `classify` now
# always reports one of "legacy"/"modern" for it too; the bare-None entry is
# gone (TYPED_BORANG_4B itself is kept in DocumentType for backward
# compatibility with already-stored rows, just no longer produced here).
CLASSIFICATION_TO_DOCUMENT_TYPE: dict[tuple[str, str, str | None], DocumentType] = {
    ("handwritten", "nikah", "legacy"): DocumentType.HANDWRITTEN_REGISTER,
    ("handwritten", "cerai", "legacy"): DocumentType.HANDWRITTEN_CERAI_LEGACY,
    ("handwritten", "cerai", "modern"): DocumentType.HANDWRITTEN_CERAI_MODERN,
    ("handwritten", "rujuk", "legacy"): DocumentType.HANDWRITTEN_RUJUK_LEGACY,
    ("handwritten", "rujuk", "modern"): DocumentType.HANDWRITTEN_RUJUK_MODERN,
    ("typed", "nikah", "legacy"): DocumentType.TYPED_NIKAH_LEGACY,
    ("typed", "nikah", "modern"): DocumentType.TYPED_NIKAH_MODERN,
    ("typed", "cerai", "legacy"): DocumentType.TYPED_CERAI_LEGACY,
    ("typed", "cerai", "modern"): DocumentType.TYPED_CERAI_MODERN,
    ("typed", "rujuk", "legacy"): DocumentType.TYPED_RUJUK_LEGACY,
    ("typed", "rujuk", "modern"): DocumentType.TYPED_RUJUK_MODERN,
}

# Mirrors marriage-ocr's own batch_runner.SUPPORTED_EXTENSIONS. Kept as a
# literal copy (not imported) since marriage-be only ever talks to
# marriage-ocr as a vendored subprocess, never as an importable dependency.
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff"}
