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
    # HANDWRITTEN_REGISTER/TYPED_BORANG_4B predate Cerai/Rujuk support and
    # map to Nikah specifically (config/handwritten.yaml, config/typed_
    # borang4b.yaml) -- kept as-is, not renamed, so existing callers/rows/
    # the OpenAPI contract don't break. The rest mirror marriage-ocr's own
    # batch_runner.ROUTING_TABLE combinations; see jobs/runner.py for the
    # config-file lookup keyed by these values.
    HANDWRITTEN_REGISTER = "HANDWRITTEN_REGISTER"
    HANDWRITTEN_CERAI_LEGACY = "HANDWRITTEN_CERAI_LEGACY"
    HANDWRITTEN_CERAI_MODERN = "HANDWRITTEN_CERAI_MODERN"
    HANDWRITTEN_RUJUK_LEGACY = "HANDWRITTEN_RUJUK_LEGACY"
    HANDWRITTEN_RUJUK_MODERN = "HANDWRITTEN_RUJUK_MODERN"
    TYPED_BORANG_4B = "TYPED_BORANG_4B"
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
