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
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.main import create_app
from marriage_ocr_api.records.repositories import create_record


class FakeExecutor:
    def submit(self, job_id: UUID) -> None:
        return None


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


def test_record_review_api_lists_and_updates_records(client: TestClient, session: Session) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614175600")
    create_job(
        session,
        id=job_id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=123,
        input_relative_path="jobs/123/input/source.pdf",
        output_relative_path="jobs/123/output/result.xlsx",
        debug_relative_path="jobs/123/debug",
        stdout_log_relative_path="jobs/123/logs/stdout.log",
        stderr_log_relative_path="jobs/123/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    record = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )
    session.commit()

    response = client.get("/api/v1/records")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    detail = client.get(f"/api/v1/records/{record.id}")
    assert detail.status_code == 200

    patch = client.patch(
        f"/api/v1/records/{record.id}",
        json={"version": 1, "field_values": {"full_name": "Ada Byron"}, "note": "corrected surname"},
    )
    assert patch.status_code == 200
    assert patch.json()["version"] == 2

    approve = client.post(f"/api/v1/records/{record.id}/approve", json={"version": 2})
    assert approve.status_code == 200
    assert approve.json()["status"] == "APPROVED"

    revisions = client.get(f"/api/v1/records/{record.id}/revisions")
    assert revisions.status_code == 200
    assert revisions.json()["total"] == 2
