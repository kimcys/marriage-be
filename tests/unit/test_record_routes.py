from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def _seed_records(session: Session) -> tuple[UUID, UUID, UUID]:
    job_id = UUID("123e4567-e89b-12d3-a456-426614175500")
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
        created_at=datetime.now(UTC) - timedelta(minutes=3),
        updated_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    first = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
        created_at=datetime.now(UTC) - timedelta(minutes=2),
        updated_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    second = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-2",
        field_values={"full_name": "Grace Hopper"},
        confidence=0.95,
        validation_issues=[],
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        updated_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    third = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-3",
        field_values={"full_name": "Katherine Johnson"},
        confidence=0.98,
        validation_issues=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.commit()
    return first.id, second.id, third.id


def test_record_routes_support_review_workflow(client: TestClient, session: Session) -> None:
    first_id, second_id, third_id = _seed_records(session)

    list_response = client.get("/api/v1/records", params={"limit": 20, "offset": 0})
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 3

    detail_response = client.get(f"/api/v1/records/{first_id}")
    assert detail_response.status_code == 200

    revisions_response = client.get(f"/api/v1/records/{first_id}/revisions")
    assert revisions_response.status_code == 200
    assert revisions_response.json()["total"] == 0

    patch_response = client.patch(
        f"/api/v1/records/{first_id}",
        json={"version": 1, "field_values": {"full_name": "Ada Byron"}, "note": "corrected surname"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["version"] == 2

    approve_response = client.post(f"/api/v1/records/{first_id}/approve", json={"version": 2})
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "APPROVED"

    reject_response = client.post(
        f"/api/v1/records/{second_id}/reject",
        json={"version": 1, "reason": "duplicate row"},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "REJECTED"

    bulk_response = client.post("/api/v1/records/bulk-approve", json={"record_ids": [str(third_id)]})
    assert bulk_response.status_code == 200
    assert bulk_response.json()["items"][0]["status"] == "APPROVED"


def test_record_routes_return_conflicts_as_api_errors(client: TestClient, session: Session) -> None:
    first_id, _, _ = _seed_records(session)

    response = client.patch(
        f"/api/v1/records/{first_id}",
        json={"version": 2, "field_values": {"full_name": "Ada Byron"}, "note": "stale version"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORD_CONFLICT"
