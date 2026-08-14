from __future__ import annotations

import sys
import time
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
from marriage_ocr_api.jobs.executor import JobExecutor
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.main import create_app


def _settings(tmp_path: Path) -> Settings:
    config_path = tmp_path / "config" / "production.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("ok: true\n", encoding="utf-8")
    return Settings(
        storage_root=tmp_path,
        ocr_python_executable=Path(sys.executable),
        ocr_module="tests.fixtures.fake_ocr_cli",
        ocr_config_path_handwritten=config_path,
        ocr_config_path_typed=config_path,
    )


def _wait_for_completed_job(client: TestClient, job_id: UUID) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        if response.status_code == 200 and response.json()["status"] == "COMPLETED":
            return
        time.sleep(0.1)
    raise AssertionError("job did not complete in time")


@pytest.mark.integration
def test_completed_job_imports_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "success")
    settings = _settings(tmp_path)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'app.db'}"
    settings.database_url = database_url
    engine = create_engine(database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.executor = JobExecutor(settings, session_factory, SubprocessOCRRunner(settings))

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_session

    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/jobs",
            files={"file": ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf")},
        )
        assert response.status_code == 202
        job_id = UUID(response.json()["id"])

        _wait_for_completed_job(client, job_id)

        records_response = client.get(f"/api/v1/jobs/{job_id}/records")
        assert records_response.status_code == 200
        assert records_response.json()["total"] == 2
    finally:
        app.state.executor.shutdown()
