from __future__ import annotations

from marriage_ocr_api.db.base import Base
from marriage_ocr_api.records.models import OCRRecord, RecordRevision


def test_record_models_register_with_metadata() -> None:
    assert OCRRecord.__tablename__ == "ocr_records"
    assert RecordRevision.__tablename__ == "record_revisions"
    assert "ocr_records" in Base.metadata.tables
    assert "record_revisions" in Base.metadata.tables
