from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.auth.repositories import create_user
from marriage_ocr_api.auth.security import hash_password
from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app


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

    # No require_user override here, deliberately -- this file exercises the
    # real login + role-gating behavior, unlike every other *_routes.py test
    # file (see tests/conftest.py::build_fake_admin_user).
    app = create_app(Settings(storage_root=tmp_path))
    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app)


def _create_user(session: Session, *, email: str, password: str, role: Role) -> None:
    create_user(session, email=email, password_hash=hash_password(password), role=role.value)
    session.commit()


def test_login_succeeds_with_correct_credentials(client: TestClient, session: Session) -> None:
    _create_user(session, email="admin@example.com", password="correct-horse", role=Role.ADMIN)

    response = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "correct-horse"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "ADMIN"
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]


def test_login_rejects_wrong_password(client: TestClient, session: Session) -> None:
    _create_user(session, email="admin@example.com", password="correct-horse", role=Role.ADMIN)

    response = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_rejects_unknown_email(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_protected_route_requires_a_token(client: TestClient) -> None:
    response = client.get("/api/v1/batches")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_protected_route_rejects_a_garbage_token(client: TestClient) -> None:
    response = client.get("/api/v1/batches", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_health_and_login_do_not_require_a_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    # Wrong credentials still reaches the real 401 INVALID_CREDENTIALS path
    # (not UNAUTHENTICATED) -- proves /auth/login itself isn't gated.
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def _login(client: TestClient, *, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return str(response.json()["access_token"])


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_reviewer_can_read_and_write_but_not_delete(client: TestClient, session: Session) -> None:
    _create_user(session, email="reviewer@example.com", password="reviewer-pass", role=Role.REVIEWER)
    token = _login(client, email="reviewer@example.com", password="reviewer-pass")

    create_response = client.post("/api/v1/batches", json={"name": "Batch 1"}, headers=_auth_headers(token))
    assert create_response.status_code == 201
    batch_id = create_response.json()["id"]

    list_response = client.get("/api/v1/batches", headers=_auth_headers(token))
    assert list_response.status_code == 200

    delete_response = client.delete(f"/api/v1/batches/{batch_id}", headers=_auth_headers(token))
    assert delete_response.status_code == 403
    assert delete_response.json()["error"]["code"] == "FORBIDDEN"


def test_admin_can_delete(client: TestClient, session: Session) -> None:
    _create_user(session, email="admin@example.com", password="admin-pass", role=Role.ADMIN)
    token = _login(client, email="admin@example.com", password="admin-pass")

    create_response = client.post("/api/v1/batches", json={"name": "Batch 1"}, headers=_auth_headers(token))
    batch_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/v1/batches/{batch_id}", headers=_auth_headers(token))
    assert delete_response.status_code == 204
