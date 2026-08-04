from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.main import create_app


class FakeExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, job_id: UUID) -> None:
        self.submitted.append(job_id)


@pytest.fixture
def engine() -> Engine:
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


def test_upload_returns_202_and_location(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        files={"file": ("register.pdf", BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"), "application/pdf")},
    )

    assert response.status_code == 202
    assert UUID(response.json()["id"])
    assert response.headers["Location"] == f"/api/v1/jobs/{response.json()['id']}"
    assert response.json()["status"] == "PENDING"


def test_invalid_upload_returns_standard_error_body(client: TestClient) -> None:
    response = client.post("/api/v1/jobs", files={"file": ("notes.txt", b"hello", "text/plain")})

    assert response.status_code == 415
    body = response.json()["error"]
    assert body["code"] == "UNSUPPORTED_FILE_TYPE"
    assert body["message"]
    assert body["request_id"]


def test_list_pagination_and_status_filter(client: TestClient, session: Session) -> None:
    base_time = datetime.now(UTC)
    create_job(
        session,
        id=UUID("123e4567-e89b-12d3-a456-426614174100"),
        status=JobStatus.PENDING,
        original_filename="older.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path="jobs/a/input/source.pdf",
        debug_relative_path="jobs/a/debug",
        stdout_log_relative_path="jobs/a/logs/stdout.log",
        stderr_log_relative_path="jobs/a/logs/stderr.log",
        ocr_git_ref="abc123",
        created_at=base_time - timedelta(minutes=1),
        updated_at=base_time - timedelta(minutes=1),
    )
    create_job(
        session,
        id=UUID("123e4567-e89b-12d3-a456-426614174101"),
        status=JobStatus.COMPLETED,
        original_filename="newer.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=2,
        input_relative_path="jobs/b/input/source.pdf",
        output_relative_path="jobs/b/output/result.xlsx",
        debug_relative_path="jobs/b/debug",
        stdout_log_relative_path="jobs/b/logs/stdout.log",
        stderr_log_relative_path="jobs/b/logs/stderr.log",
        ocr_git_ref="abc123",
        created_at=base_time,
        updated_at=base_time,
    )
    session.commit()

    response = client.get("/api/v1/jobs", params={"status": "COMPLETED", "limit": 1, "offset": 0})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert [item["status"] for item in payload["items"]] == ["COMPLETED"]


def test_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/123e4567-e89b-12d3-a456-426614174999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_download_before_completion_returns_409(client: TestClient, session: Session, tmp_path: Path) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614174200")
    create_job(
        session,
        id=job_id,
        status=JobStatus.PROCESSING,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.commit()

    response = client.get(f"/api/v1/jobs/{job_id}/download")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_NOT_COMPLETED"


def test_completed_job_download_returns_xlsx_headers(
    client: TestClient,
    session: Session,
    tmp_path: Path,
) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614174201")
    output_path = tmp_path / "jobs" / str(job_id) / "output" / "result.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"fake-xlsx")
    create_job(
        session,
        id=job_id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        output_relative_path=f"jobs/{job_id}/output/result.xlsx",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.commit()

    response = client.get(f"/api/v1/jobs/{job_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].endswith('register-result.xlsx"')


def test_completed_job_missing_output_returns_410(client: TestClient, session: Session) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614174202")
    create_job(
        session,
        id=job_id,
        status=JobStatus.COMPLETED,
        original_filename="register.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        output_relative_path=f"jobs/{job_id}/output/result.xlsx",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
        ocr_git_ref="abc123",
    )
    session.commit()

    response = client.get(f"/api/v1/jobs/{job_id}/download")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "OUTPUT_FILE_MISSING"
