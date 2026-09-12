from __future__ import annotations

from enum import StrEnum


class BatchStatus(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExportStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DocumentType(StrEnum):
    # HANDWRITTEN_REGISTER predates Cerai/Rujuk support and maps to Nikah
    # specifically (config/handwritten.yaml) -- kept as-is, not renamed, so
    # existing callers/rows/the OpenAPI contract don't break. The rest
    # mirror marriage-ocr's own batch_runner.ROUTING_TABLE combinations;
    # see jobs/runner.py for the config-file lookup keyed by these values.
    #
    # TYPED_BORANG_4B is the same kind of holdover: typed Nikah used to be
    # one undifferentiated type (marriage-ocr's own typed/template.py had a
    # single, uncalibrated "borang_4b" template). marriage-ocr has since
    # split it into TYPED_NIKAH_LEGACY (Borang 3A, pre-2003) and
    # TYPED_NIKAH_MODERN (Borang 4B, post-2003) -- the same legacy/modern
    # split Cerai/Rujuk already had. TYPED_BORANG_4B is kept (not removed)
    # for backward compatibility with any already-stored rows/OpenAPI
    # consumers; new auto-classification (onedrive/classification.py) never
    # produces it any more.
    HANDWRITTEN_REGISTER = "HANDWRITTEN_REGISTER"
    HANDWRITTEN_CERAI_LEGACY = "HANDWRITTEN_CERAI_LEGACY"
    HANDWRITTEN_CERAI_MODERN = "HANDWRITTEN_CERAI_MODERN"
    HANDWRITTEN_RUJUK_LEGACY = "HANDWRITTEN_RUJUK_LEGACY"
    HANDWRITTEN_RUJUK_MODERN = "HANDWRITTEN_RUJUK_MODERN"
    TYPED_BORANG_4B = "TYPED_BORANG_4B"
    TYPED_NIKAH_LEGACY = "TYPED_NIKAH_LEGACY"
    TYPED_NIKAH_MODERN = "TYPED_NIKAH_MODERN"
    TYPED_CERAI_LEGACY = "TYPED_CERAI_LEGACY"
    TYPED_CERAI_MODERN = "TYPED_CERAI_MODERN"
    TYPED_RUJUK_LEGACY = "TYPED_RUJUK_LEGACY"
    TYPED_RUJUK_MODERN = "TYPED_RUJUK_MODERN"


# Handwritten multi-page PDFs get split one job per page (see
# batches/routers.py::_split_page_count) -- typed forms never do, regardless
# of record type, since process-typed handles their fixed page count itself.
HANDWRITTEN_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.HANDWRITTEN_REGISTER,
        DocumentType.HANDWRITTEN_CERAI_LEGACY,
        DocumentType.HANDWRITTEN_CERAI_MODERN,
        DocumentType.HANDWRITTEN_RUJUK_LEGACY,
        DocumentType.HANDWRITTEN_RUJUK_MODERN,
    }
)

# Every DocumentType that runs through marriage-ocr's `process-typed` CLI
# command (jobs/runner.py's _CLI_COMMAND_BY_DOCUMENT_TYPE) rather than
# `process` -- these always write CSV output, never XLSX. jobs/processing.py
# uses this (not a single `== TYPED_BORANG_4B` check, which only ever
# covered one of six typed types) to decide both the output file extension
# and which importer (import_records_from_csv vs. _xlsx) to run.
TYPED_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.TYPED_BORANG_4B,
        DocumentType.TYPED_NIKAH_LEGACY,
        DocumentType.TYPED_NIKAH_MODERN,
        DocumentType.TYPED_CERAI_LEGACY,
        DocumentType.TYPED_CERAI_MODERN,
        DocumentType.TYPED_RUJUK_LEGACY,
        DocumentType.TYPED_RUJUK_MODERN,
    }
)
