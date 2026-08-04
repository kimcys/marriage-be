from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.main import create_app


def _client(settings: Settings, session: Session) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app)


def _session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return session_factory()


def test_ready_route_returns_200_when_all_checks_pass(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "production.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("ok: true\n", encoding="utf-8")
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        storage_root=storage_root,
        ocr_config_path=config_path,
        ocr_python_executable=Path(sys.executable),
    )
    session = _session()

    client = _client(settings, session)
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": "ok",
            "storage": "ok",
            "ocr_config": "ok",
            "ocr_python": "ok",
        },
    }


def test_ready_route_returns_503_when_config_is_missing(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        storage_root=storage_root,
        ocr_config_path=tmp_path / "missing" / "production.yaml",
        ocr_python_executable=Path(sys.executable),
    )
    session = _session()

    client = _client(settings, session)
    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not ready"
    assert response.json()["checks"]["ocr_config"] == "failed"
