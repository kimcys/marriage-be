from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.auth.dependencies import require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.repositories import create_user
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.main import create_app
from marriage_ocr_api.records.repositories import create_record, list_revisions


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _client(session_factory, tmp_path: Path, user: User) -> TestClient:
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


def _seed_record(session_factory, batch_id: str) -> UUID:
    with session_factory() as db:
        job_id = UUID("123e4567-e89b-12d3-a456-426614179999")
        create_job(
            db,
            id=job_id,
            batch_id=UUID(batch_id),
            status=JobStatus.COMPLETED,
            original_filename="image00012.jpg",
            stored_filename="source.jpg",
            content_type="image/jpeg",
            file_size_bytes=1,
            input_relative_path="jobs/1/input/source.jpg",
            output_relative_path="jobs/1/output/result.xlsx",
            debug_relative_path="jobs/1/debug",
            stdout_log_relative_path="jobs/1/logs/stdout.log",
            stderr_log_relative_path="jobs/1/logs/stderr.log",
            ocr_git_ref="abc123",
        )
        record = create_record(
            db,
            job_id=job_id,
            batch_id=UUID(batch_id),
            source_key="page-1-row-1",
            field_values={"Nama Suami": "Ahmad", "No KP Suami": "900101"},
            confidence=0.9,
            validation_issues=[],
        )
        db.commit()
        return record.id


def test_every_change_is_recorded_against_the_logged_in_user(session_factory, tmp_path: Path) -> None:
    with session_factory() as db:
        admin = create_user(db, email="owner@example.com", password_hash="x", role="ADMIN", name="Aiman")
        db.commit()
    client = _client(session_factory, tmp_path, admin)

    batch_id = client.post("/api/v1/batches", json={"name": "Klang 2009"}).json()["id"]
    client.patch(f"/api/v1/batches/{batch_id}", json={"name": "Klang 2009", "daerah": "Klang", "negeri": "Selangor"})
    record_id = _seed_record(session_factory, batch_id)
    corrected = client.patch(
        f"/api/v1/records/{record_id}", json={"version": 1, "field_values": {"Nama Suami": "Ahmad Bin Ali"}}
    )
    assert corrected.status_code == 200
    # A client-supplied reviewer name must not be what gets recorded.
    approved = client.post("/api/v1/records/bulk-approve", json={"record_ids": [str(record_id)], "reviewer": "someone"})
    assert approved.status_code == 200
    client.post(f"/api/v1/batches/{batch_id}/cancel")
    assert client.delete(f"/api/v1/batches/{batch_id}").status_code == 204

    with session_factory() as db:
        assert [rev.reviewer for rev in list_revisions(db, record_id)] == []  # deleted with the batch

    activity = client.get("/api/v1/activity").json()
    actions = [item["action"] for item in activity["items"]]
    assert actions == [
        "batch.deleted",
        "batch.processing_stopped",
        "record.bulk_approved",
        "record.corrected",
        "batch.updated",
        "batch.created",
    ]
    assert activity["total"] == 6
    assert {item["user_code"] for item in activity["items"]} == {"MOCR001"}
    assert {item["user_name"] for item in activity["items"]} == {"Aiman"}

    by_action = {item["action"]: item for item in activity["items"]}
    assert by_action["record.corrected"]["details"]["changes"] == {"Nama Suami": ["Ahmad", "Ahmad Bin Ali"]}
    assert by_action["record.corrected"]["target_label"] == "image00012.jpg · page-1-row-1"
    assert by_action["batch.updated"]["details"]["changes"] == {
        "daerah": [None, "Klang"],
        "negeri": [None, "Selangor"],
    }
    # The deletion entry survives the batch it describes.
    assert by_action["batch.deleted"]["target_label"] == "Klang 2009"
    assert by_action["batch.deleted"]["batch_id"] == batch_id

    filtered = client.get("/api/v1/activity", params={"action": "batch.created"}).json()
    assert [item["action"] for item in filtered["items"]] == ["batch.created"]

    users = client.get("/api/v1/users").json()
    assert [(u["id"], u["code"], u["name"], u["role"]) for u in users] == [(str(admin.id), "MOCR001", "Aiman", "ADMIN")]


def test_corrections_record_the_users_code_as_reviewer(session_factory, tmp_path: Path) -> None:
    with session_factory() as db:
        staff = create_user(db, email="staff@example.com", password_hash="x", role="REVIEWER", name="Siti")
        db.commit()
    client = _client(session_factory, tmp_path, staff)
    batch_id = client.post("/api/v1/batches", json={"name": "B"}).json()["id"]
    record_id = _seed_record(session_factory, batch_id)

    client.patch(f"/api/v1/records/{record_id}", json={"version": 1, "field_values": {"Nama Suami": "X"}})

    with session_factory() as db:
        assert [rev.reviewer for rev in list_revisions(db, record_id)] == ["MOCR001"]


def test_only_admins_can_read_the_activity_log(session_factory, tmp_path: Path) -> None:
    with session_factory() as db:
        staff = create_user(db, email="staff@example.com", password_hash="x", role="REVIEWER")
        db.commit()
    client = _client(session_factory, tmp_path, staff)

    assert client.get("/api/v1/activity").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
