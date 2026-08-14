from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.repositories import create_batch, create_export
from marriage_ocr_api.batches.status import ExportStatus
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.exports.models import Export
from marriage_ocr_api.exports.repositories import get_export, list_exports


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


def test_export_model_registers_with_metadata() -> None:
    assert Export.__tablename__ == "exports"
    assert "exports" in Base.metadata.tables


def test_list_and_get_export(session: Session) -> None:
    batch = create_batch(session, name="Batch 1", description=None, created_by=UUID(int=1))
    export = create_export(session, batch_id=batch.id, format="CSV", created_by=UUID(int=1))

    found = get_export(session, export.id)
    listed = list_exports(session, limit=20, offset=0)

    assert found is not None
    assert found.id == export.id
    assert len(listed) == 1
    assert listed[0].status == ExportStatus.PENDING.value
