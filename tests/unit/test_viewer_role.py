from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.auth.dependencies import require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.repositories import create_user
from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(session_factory, tmp_path: Path, role: Role) -> TestClient:
    with session_factory() as db:
        user: User = create_user(db, email=f"{role.value.lower()}@example.com", password_hash="x", role=role.value)

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app(Settings(storage_root=tmp_path))
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


WRITE_ROUTES = [
    ("POST", "/api/v1/batches", {"name": "Fake batch"}),
    ("PATCH", f"/api/v1/batches/{uuid4()}", {"name": "Renamed"}),
    ("POST", f"/api/v1/batches/{uuid4()}/cancel", None),
    ("POST", "/api/v1/exports", {"format": "CSV"}),
    ("PATCH", f"/api/v1/records/{uuid4()}", {"fields": {}}),
    ("POST", f"/api/v1/records/{uuid4()}/approve", None),
    ("POST", "/api/v1/records/bulk-approve", {"record_ids": []}),
    ("POST", f"/api/v1/jobs/{uuid4()}/retry", None),
    ("POST", f"/api/v1/batches/{uuid4()}/onedrive-links", {"url": "https://1drv.ms/f/fake"}),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
def test_viewer_cannot_write(session_factory, tmp_path: Path, method: str, path: str, body) -> None:
    response = _client(session_factory, tmp_path, Role.VIEWER).request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_viewer_can_read(session_factory, tmp_path: Path) -> None:
    client = _client(session_factory, tmp_path, Role.VIEWER)

    assert client.get("/api/v1/batches").status_code == 200
    assert client.get("/api/v1/records").status_code == 200
    assert client.get("/api/v1/exports").status_code == 200
