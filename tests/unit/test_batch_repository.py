from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.models import Batch, Document, Export
from marriage_ocr_api.batches.repositories import create_batch, create_document, create_export
from marriage_ocr_api.batches.status import BatchStatus, DocumentStatus, ExportStatus
from marriage_ocr_api.db.base import Base


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_batch_and_export_models_register_with_metadata() -> None:
    assert Batch.__tablename__ == "batches"
    assert Document.__tablename__ == "documents"
    assert Export.__tablename__ == "exports"
    assert "batches" in Base.metadata.tables
    assert "documents" in Base.metadata.tables
    assert "exports" in Base.metadata.tables


def test_create_batch_document_and_export(session: Session) -> None:
    batch = create_batch(session, name="Batch 1", description="Example batch", created_by=UUID(int=1))
    document = create_document(
        session,
        batch_id=batch.id,
        original_filename="register.pdf",
        safe_filename="register.pdf",
        media_type="application/pdf",
        size_bytes=123,
        sha256="abc123",
        storage_key="batches/1/documents/1/input/register.pdf",
        status=DocumentStatus.UPLOADED,
    )
    export = create_export(
        session,
        batch_id=batch.id,
        format="XLSX",
        created_by=UUID(int=1),
    )

    assert batch.status == BatchStatus.DRAFT.value
    assert document.status == DocumentStatus.UPLOADED.value
    assert export.status == ExportStatus.PENDING.value
