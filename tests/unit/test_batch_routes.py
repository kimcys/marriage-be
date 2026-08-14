from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app


class FakeExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, job_id: UUID) -> None:
        self.submitted.append(job_id)


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
    app.state.executor = FakeExecutor()
    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def test_create_and_list_batches(client: TestClient) -> None:
    response = client.post("/api/v1/batches", json={"name": "Batch 1", "description": "Example batch"})
    assert response.status_code == 201
    batch_id = response.json()["id"]
    assert UUID(batch_id)

    listed = client.get("/api/v1/batches")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == batch_id


def test_upload_document_submits_job_to_executor(client: TestClient) -> None:
    batch_response = client.post("/api/v1/batches", json={"name": "Batch 1"})
    batch_id = batch_response.json()["id"]

    upload_response = client.post(
        f"/api/v1/batches/{batch_id}/documents",
        files={"file": ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf")},
    )

    assert upload_response.status_code == 202
    executor = client.app.state.executor
    assert len(executor.submitted) == 1
