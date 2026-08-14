from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine) -> Session:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(engine, tmp_path: Path) -> TestClient:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app(Settings(storage_root=tmp_path))
    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def test_create_export_and_download(client: TestClient) -> None:
    batch_response = client.post("/api/v1/batches", json={"name": "Batch 1"})
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["id"]

    export_response = client.post(
        "/api/v1/exports",
        json={"batch_id": batch_id, "format": "XLSX", "include_unreviewed": False},
    )
    assert export_response.status_code == 202
    export_id = export_response.json()["id"]

    download_response = client.get(f"/api/v1/exports/{export_id}/download")
    assert download_response.status_code == 200
