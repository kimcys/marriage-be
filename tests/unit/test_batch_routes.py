from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.batches.repositories import create_document, create_export, get_batch, get_document, get_export
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job, get_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.main import create_app
from marriage_ocr_api.onedrive.repositories import create_submission, get_submission
from marriage_ocr_api.records.repositories import create_record, get_record
from marriage_ocr_api.records.service import apply_correction


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


def test_delete_batch_cascades_everything_and_removes_storage(
    client: TestClient, session: Session, tmp_path: Path
) -> None:
    batch_id = UUID(client.post("/api/v1/batches", json={"name": "Batch 1"}).json()["id"])

    document = create_document(
        session,
        batch_id=batch_id,
        original_filename="register.pdf",
        safe_filename="register.pdf",
        media_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        storage_key=f"batches/{batch_id}/documents/doc/input/register.pdf",
    )
    submission = create_submission(session, batch_id=batch_id, url="https://1drv.ms/f/s!to-delete")
    job_id = UUID("123e4567-e89b-12d3-a456-426614175700")
    create_job(
        session,
        id=job_id,
        batch_id=batch_id,
        document_id=document.id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="register.pdf",
        content_type="application/pdf",
        file_size_bytes=10,
        input_relative_path=f"batches/{batch_id}/documents/doc/input/register.pdf",
        output_relative_path=f"batches/{batch_id}/documents/doc/output/result.xlsx",
        debug_relative_path=f"batches/{batch_id}/documents/doc/debug",
        stdout_log_relative_path=f"batches/{batch_id}/documents/doc/logs/stdout.log",
        stderr_log_relative_path=f"batches/{batch_id}/documents/doc/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    record = create_record(
        session,
        job_id=job_id,
        batch_id=batch_id,
        document_id=document.id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )
    export = create_export(
        session,
        batch_id=batch_id,
        format="XLSX",
        created_by=None,
        storage_key=f"exports/{batch_id}/export.xlsx",
    )
    session.commit()
    apply_correction(
        session,
        record.id,
        expected_version=1,
        field_values={"full_name": "Ada Byron"},
        reviewer=None,
        note="correction to create a revision",
    )
    session.commit()

    # Files a real run would have produced -- proves the on-disk cleanup
    # actually removes them, not just the DB rows.
    document_file = tmp_path / "batches" / str(batch_id) / "documents" / "doc" / "input" / "register.pdf"
    document_file.parent.mkdir(parents=True, exist_ok=True)
    document_file.write_bytes(b"pdf")
    output_file = tmp_path / "batches" / str(batch_id) / "documents" / "doc" / "output" / "result.xlsx"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_bytes(b"xlsx")

    response = client.delete(f"/api/v1/batches/{batch_id}")

    # The delete happened through the API's own session -- this session
    # still has its own (now stale) identity map entries for the objects
    # created directly through it above. expire_all() would try to refresh
    # them and raise ObjectDeletedError; expunge_all() forces a clean requery.
    session.expunge_all()

    assert response.status_code == 204
    assert get_batch(session, batch_id) is None
    assert get_document(session, document.id) is None
    assert get_submission(session, submission.id) is None
    assert get_job(session, job_id) is None
    assert get_record(session, record.id) is None
    assert get_export(session, export.id) is None
    assert not (tmp_path / "batches" / str(batch_id)).exists()


def test_delete_missing_batch_returns_404(client: TestClient) -> None:
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = client.delete(f"/api/v1/batches/{missing_id}")
    assert response.status_code == 404


def test_download_document_serves_the_original_source_file(
    client: TestClient, session: Session, tmp_path: Path
) -> None:
    batch_id = UUID(client.post("/api/v1/batches", json={"name": "Batch 1"}).json()["id"])
    document = create_document(
        session,
        batch_id=batch_id,
        original_filename="register.pdf",
        safe_filename="register.pdf",
        media_type="application/pdf",
        size_bytes=3,
        sha256="0" * 64,
        storage_key=f"batches/{batch_id}/documents/doc/input/register.pdf",
    )
    session.commit()
    source_file = tmp_path / "batches" / str(batch_id) / "documents" / "doc" / "input" / "register.pdf"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"pdf")

    response = client.get(f"/api/v1/batches/{batch_id}/documents/{document.id}/download")

    assert response.status_code == 200
    assert response.content == b"pdf"
    assert response.headers["content-type"] == "application/pdf"


def test_download_document_for_missing_document_returns_404(client: TestClient) -> None:
    batch_id = UUID(client.post("/api/v1/batches", json={"name": "Batch 1"}).json()["id"])
    missing_document_id = "00000000-0000-0000-0000-000000000000"

    response = client.get(f"/api/v1/batches/{batch_id}/documents/{missing_document_id}/download")

    assert response.status_code == 404


def test_download_document_scoped_to_the_wrong_batch_returns_404(client: TestClient, session: Session) -> None:
    batch_id = UUID(client.post("/api/v1/batches", json={"name": "Batch 1"}).json()["id"])
    other_batch_id = UUID(client.post("/api/v1/batches", json={"name": "Batch 2"}).json()["id"])
    document = create_document(
        session,
        batch_id=batch_id,
        original_filename="register.pdf",
        safe_filename="register.pdf",
        media_type="application/pdf",
        size_bytes=3,
        sha256="0" * 64,
        storage_key=f"batches/{batch_id}/documents/doc/input/register.pdf",
    )
    session.commit()

    response = client.get(f"/api/v1/batches/{other_batch_id}/documents/{document.id}/download")

    assert response.status_code == 404
