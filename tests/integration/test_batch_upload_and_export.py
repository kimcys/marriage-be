from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import boto3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app
from marriage_ocr_api.storage.factory import get_storage_service
from marriage_ocr_api.storage.s3 import S3StorageService


class FakeExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, job_id: UUID) -> None:
        self.submitted.append(job_id)

    def shutdown(self) -> None:
        pass


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


def test_create_batch_upload_document_create_export_and_download(client: TestClient) -> None:
    batch_response = client.post("/api/v1/batches", json={"name": "Batch 1"})
    assert batch_response.status_code == 201

    batch_id = batch_response.json()["id"]

    upload_response = client.post(
        f"/api/v1/batches/{batch_id}/documents",
        files={"file": ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf")},
    )
    assert upload_response.status_code == 202

    export_response = client.post(
        "/api/v1/exports",
        json={"batch_id": batch_id, "format": "XLSX", "include_unreviewed": False},
    )
    assert export_response.status_code == 202
    export_id = export_response.json()["id"]

    download_response = client.get(f"/api/v1/exports/{export_id}/download")
    assert download_response.status_code == 200


def test_export_download_round_trips_through_real_minio(engine, tmp_path: Path) -> None:
    endpoint_url = os.environ.get("MINIO_ENDPOINT_URL")
    if not endpoint_url:
        pytest.skip("MINIO profile is not enabled")
    bucket_name = os.environ.get("MINIO_BUCKET_NAME", "exports-test")
    access_key_id = os.environ.get("MINIO_ACCESS_KEY_ID", "minio")
    secret_access_key = os.environ.get("MINIO_SECRET_ACCESS_KEY", "minio123")
    region_name = os.environ.get("MINIO_REGION_NAME", "us-east-1")

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    settings = Settings(
        storage_root=tmp_path,
        storage_backend="s3",
        minio_endpoint_url=endpoint_url,
        minio_bucket_name=bucket_name,
        minio_access_key_id=access_key_id,
        minio_secret_access_key=secret_access_key,
        minio_region_name=region_name,
    )
    # Provision the bucket the way ops would in a real deployment (once, out-of-band) --
    # avoids also having to route the FastAPI lifespan's real Postgres connection
    # through this test's in-memory SQLite engine just to reach the S3 startup step.
    storage = get_storage_service(settings)
    assert isinstance(storage, S3StorageService)
    storage.ensure_bucket_exists()

    app = create_app(settings)
    app.state.executor = FakeExecutor()
    app.dependency_overrides[get_db_session] = override_session
    client = TestClient(app)

    batch_response = client.post("/api/v1/batches", json={"name": "S3 batch"})
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["id"]

    upload_response = client.post(
        f"/api/v1/batches/{batch_id}/documents",
        files={"file": ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf")},
    )
    assert upload_response.status_code == 202

    export_response = client.post(
        "/api/v1/exports",
        json={"batch_id": batch_id, "format": "XLSX", "include_unreviewed": False},
    )
    assert export_response.status_code == 202
    export_id = export_response.json()["id"]

    export_status = client.get(f"/api/v1/exports/{export_id}").json()
    storage_key = export_status["storage_key"]

    # Independent check against MinIO itself: the export must actually be there,
    # not just something the API layer claims succeeded.
    boto_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )
    boto_client.head_object(Bucket=bucket_name, Key=storage_key)

    download_response = client.get(f"/api/v1/exports/{export_id}/download", follow_redirects=False)
    assert download_response.status_code in (200, 307)
