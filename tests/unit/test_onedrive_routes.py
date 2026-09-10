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
from marriage_ocr_api.main import create_app
from marriage_ocr_api.onedrive.repositories import mark_failed


class FakeOneDriveExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, submission_id: UUID) -> None:
        self.submitted.append(submission_id)


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
def client(engine, tmp_path: Path) -> TestClient:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app(Settings(storage_root=tmp_path))
    app.state.executor = None
    app.state.onedrive_executor = FakeOneDriveExecutor()
    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def _create_batch(client: TestClient) -> str:
    response = client.post("/api/v1/batches", json={"name": "Batch 1"})
    assert response.status_code == 201
    return response.json()["id"]


def _fail_submission(engine, submission_id: str) -> None:
    """Directly flips a submission to FAILED, bypassing the (fake, always-
    succeeding) executor -- there's no other way to get a submission into
    FAILED through this test client's HTTP surface alone."""
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        mark_failed(session, UUID(submission_id), error_code="ONEDRIVE_FETCH_FAILED", error_message="sign-in required")
        session.commit()
    finally:
        session.close()


def test_submit_onedrive_link_creates_pending_submission(client: TestClient) -> None:
    batch_id = _create_batch(client)

    response = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!abc"})

    assert response.status_code == 202
    payload = response.json()
    assert payload["batch_id"] == batch_id
    assert payload["url"] == "https://1drv.ms/f/s!abc"
    assert payload["status"] == "PENDING"
    executor: FakeOneDriveExecutor = client.app.state.onedrive_executor
    assert len(executor.submitted) == 1
    assert str(executor.submitted[0]) == payload["id"]


def test_submit_duplicate_url_returns_existing_submission_without_refetch(client: TestClient) -> None:
    batch_id = _create_batch(client)

    first = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!dup"})
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!dup"})
    assert second.status_code == 200
    assert second.json()["id"] == first_id

    executor: FakeOneDriveExecutor = client.app.state.onedrive_executor
    assert len(executor.submitted) == 1


def test_submit_duplicate_url_from_a_different_batch_still_returns_original(client: TestClient) -> None:
    batch_id = _create_batch(client)
    other_batch_id = _create_batch(client)

    first = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!shared"})
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = client.post(
        f"/api/v1/batches/{other_batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!shared"}
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.json()["batch_id"] == batch_id


def test_submit_duplicate_url_resumes_a_failed_submission(client: TestClient, engine) -> None:
    batch_id = _create_batch(client)

    first = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!resume"})
    submission_id = first.json()["id"]
    _fail_submission(engine, submission_id)

    second = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!resume"})

    assert second.status_code == 202
    payload = second.json()
    assert payload["id"] == submission_id
    assert payload["status"] == "PENDING"
    assert payload["error"] is None
    executor: FakeOneDriveExecutor = client.app.state.onedrive_executor
    assert [str(job_id) for job_id in executor.submitted] == [submission_id, submission_id]


def test_submit_onedrive_link_for_missing_batch_returns_404(client: TestClient) -> None:
    missing_batch_id = "00000000-0000-0000-0000-000000000000"
    response = client.post(f"/api/v1/batches/{missing_batch_id}/onedrive-links", json={"url": "https://1drv.ms/x"})
    assert response.status_code == 404


def test_list_onedrive_links_returns_newest_first_and_scoped_to_batch(client: TestClient) -> None:
    batch_id = _create_batch(client)
    other_batch_id = _create_batch(client)

    client.post(f"/api/v1/batches/{other_batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!other"})
    client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!first"})
    client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!second"})

    response = client.get(f"/api/v1/batches/{batch_id}/onedrive-links")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["url"] for item in payload["items"]] == [
        "https://1drv.ms/f/s!second",
        "https://1drv.ms/f/s!first",
    ]
    assert all(item["batch_id"] == batch_id for item in payload["items"])


def test_list_onedrive_links_paginates(client: TestClient) -> None:
    batch_id = _create_batch(client)
    for i in range(3):
        client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": f"https://1drv.ms/f/s!link{i}"})

    response = client.get(f"/api/v1/batches/{batch_id}/onedrive-links", params={"limit": 2, "offset": 0})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["limit"] == 2
    assert len(payload["items"]) == 2


def test_list_onedrive_links_for_missing_batch_returns_404(client: TestClient) -> None:
    missing_batch_id = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/v1/batches/{missing_batch_id}/onedrive-links")
    assert response.status_code == 404


def test_retry_onedrive_link_resubmits_a_failed_submission(client: TestClient, engine) -> None:
    batch_id = _create_batch(client)
    created = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!retry"})
    submission_id = created.json()["id"]
    _fail_submission(engine, submission_id)

    response = client.post(f"/api/v1/batches/{batch_id}/onedrive-links/{submission_id}/retry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "PENDING"
    assert payload["error"] is None
    executor: FakeOneDriveExecutor = client.app.state.onedrive_executor
    assert str(executor.submitted[-1]) == submission_id


def test_retry_onedrive_link_rejects_a_submission_that_is_not_failed(client: TestClient) -> None:
    batch_id = _create_batch(client)
    created = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!pending"})
    submission_id = created.json()["id"]

    response = client.post(f"/api/v1/batches/{batch_id}/onedrive-links/{submission_id}/retry")

    assert response.status_code == 409


def test_retry_onedrive_link_for_missing_submission_returns_404(client: TestClient) -> None:
    batch_id = _create_batch(client)
    missing_submission_id = "00000000-0000-0000-0000-000000000000"

    response = client.post(f"/api/v1/batches/{batch_id}/onedrive-links/{missing_submission_id}/retry")

    assert response.status_code == 404


def test_retry_onedrive_link_scoped_to_the_wrong_batch_returns_404(client: TestClient) -> None:
    batch_id = _create_batch(client)
    other_batch_id = _create_batch(client)
    created = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!scoped"})
    submission_id = created.json()["id"]

    response = client.post(f"/api/v1/batches/{other_batch_id}/onedrive-links/{submission_id}/retry")

    assert response.status_code == 404


def test_delete_onedrive_link_removes_it_from_the_list(client: TestClient) -> None:
    batch_id = _create_batch(client)
    created = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!delete"})
    submission_id = created.json()["id"]

    response = client.delete(f"/api/v1/batches/{batch_id}/onedrive-links/{submission_id}")

    assert response.status_code == 204
    listing = client.get(f"/api/v1/batches/{batch_id}/onedrive-links")
    assert listing.json()["total"] == 0


def test_delete_onedrive_link_for_missing_submission_returns_404(client: TestClient) -> None:
    batch_id = _create_batch(client)
    missing_submission_id = "00000000-0000-0000-0000-000000000000"

    response = client.delete(f"/api/v1/batches/{batch_id}/onedrive-links/{missing_submission_id}")

    assert response.status_code == 404


def test_delete_onedrive_link_scoped_to_the_wrong_batch_returns_404(client: TestClient) -> None:
    batch_id = _create_batch(client)
    other_batch_id = _create_batch(client)
    created = client.post(f"/api/v1/batches/{batch_id}/onedrive-links", json={"url": "https://1drv.ms/f/s!wrongbatch"})
    submission_id = created.json()["id"]

    response = client.delete(f"/api/v1/batches/{other_batch_id}/onedrive-links/{submission_id}")

    assert response.status_code == 404
