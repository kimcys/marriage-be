from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.executor import JobExecutor
from marriage_ocr_api.jobs.paths import build_job_paths
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.jobs.status import JobStatus
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
        # Standalone job (no batch/document), seeded directly rather than
        # through an upload endpoint -- this test is about the real
        # executor completing a job and importing its records, not about
        # how the job's input file arrived on disk.
        job_id = uuid4()
        paths = build_job_paths(settings.storage_root, job_id)
        paths.input_dir.mkdir(parents=True, exist_ok=True)
        paths.input_source_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")
        with session_factory() as session:
            create_job(
                session,
                id=job_id,
                status=JobStatus.PENDING,
                original_filename="register.pdf",
                stored_filename=paths.input_source_path.name,
                content_type="application/pdf",
                file_size_bytes=paths.input_source_path.stat().st_size,
                input_relative_path=paths.input_relative_path,
                debug_relative_path=paths.debug_relative_path,
                stdout_log_relative_path=paths.stdout_log_relative_path,
                stderr_log_relative_path=paths.stderr_log_relative_path,
                ocr_git_ref="test",
            )
            session.commit()
        app.state.executor.submit(job_id)

        _wait_for_completed_job(client, job_id)

        records_response = client.get(f"/api/v1/jobs/{job_id}/records")
        assert records_response.status_code == 200
        assert records_response.json()["total"] == 2
    finally:
        app.state.executor.shutdown()
