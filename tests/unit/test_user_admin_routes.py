from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.auth.dependencies import require_user
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.repositories import create_user, get_user
from marriage_ocr_api.auth.security import hash_password
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.main import create_app


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _app(session_factory, tmp_path: Path):
    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app(Settings(storage_root=tmp_path, jwt_secret_key="test-secret-that-is-at-least-32-bytes-long"))
    app.dependency_overrides[get_db_session] = override_session
    return app


def _as(app, user: User) -> TestClient:
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _admin(session_factory) -> User:
    with session_factory() as db:
        admin = create_user(
            db, email="owner@example.com", password_hash=hash_password("owner-pass"), role="ADMIN", name="Aiman"
        )
        db.commit()
        return admin


def test_admin_creates_edits_and_resets_a_users_password(session_factory, tmp_path: Path) -> None:
    app = _app(session_factory, tmp_path)
    admin_client = _as(app, _admin(session_factory))

    created = admin_client.post(
        "/api/v1/users",
        json={"email": "Siti@Example.com", "name": "Siti", "role": "REVIEWER", "password": "first-pass"},
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["code"], body["email"], body["role"], body["is_active"]) == (
        "MOCR002",
        "siti@example.com",
        "REVIEWER",
        True,
    )
    duplicate = admin_client.post(
        "/api/v1/users", json={"email": "siti@example.com", "name": "S", "role": "REVIEWER", "password": "whatever1"}
    )
    assert duplicate.status_code == 409
    too_short = admin_client.post(
        "/api/v1/users", json={"email": "x@example.com", "name": "X", "role": "REVIEWER", "password": "short"}
    )
    assert too_short.status_code == 422

    user_id = body["id"]
    updated = admin_client.patch(f"/api/v1/users/{user_id}", json={"name": "Siti Aminah", "role": "ADMIN"})
    assert (updated.json()["name"], updated.json()["role"]) == ("Siti Aminah", "ADMIN")

    assert admin_client.post(f"/api/v1/users/{user_id}/password", json={"password": "second-pass"}).status_code == 204
    login = TestClient(app).post("/api/v1/auth/login", json={"email": "siti@example.com", "password": "second-pass"})
    assert login.status_code == 200

    actions = [item["action"] for item in admin_client.get("/api/v1/activity").json()["items"]]
    assert actions == ["user.password_reset", "user.updated", "user.created"]


def test_disabled_user_can_no_longer_log_in_or_use_their_token(session_factory, tmp_path: Path) -> None:
    app = _app(session_factory, tmp_path)
    admin = _admin(session_factory)
    with session_factory() as db:
        staff = create_user(db, email="staff@example.com", password_hash=hash_password("staff-pass"), role="REVIEWER")
        db.commit()
    admin_client = _as(app, admin)

    disabled = admin_client.patch(f"/api/v1/users/{staff.id}", json={"is_active": False})
    assert disabled.json()["is_active"] is False
    assert admin_client.get("/api/v1/activity").json()["items"][0]["action"] == "user.disabled"

    app.dependency_overrides.pop(require_user)
    anonymous = TestClient(app)
    assert (
        anonymous.post("/api/v1/auth/login", json={"email": "staff@example.com", "password": "staff-pass"}).status_code
        == 401
    )

    admin_client = _as(app, admin)
    admin_client.patch(f"/api/v1/users/{staff.id}", json={"is_active": True})
    app.dependency_overrides.pop(require_user)
    assert (
        TestClient(app)
        .post("/api/v1/auth/login", json={"email": "staff@example.com", "password": "staff-pass"})
        .status_code
        == 200
    )


def test_admin_cannot_lock_themselves_or_everyone_out(session_factory, tmp_path: Path) -> None:
    app = _app(session_factory, tmp_path)
    admin = _admin(session_factory)
    client = _as(app, admin)

    assert client.patch(f"/api/v1/users/{admin.id}", json={"role": "REVIEWER"}).json()["error"]["code"] == (
        "CANNOT_CHANGE_OWN_ROLE"
    )
    assert client.patch(f"/api/v1/users/{admin.id}", json={"is_active": False}).json()["error"]["code"] == (
        "CANNOT_DISABLE_SELF"
    )
    with session_factory() as db:
        assert get_user(db, admin.id).role == "ADMIN"

    # Another admin can be demoted or disabled by this one.
    with session_factory() as db:
        other = create_user(db, email="b@example.com", password_hash="x", role="ADMIN")
        db.commit()
    assert client.patch(f"/api/v1/users/{other.id}", json={"role": "REVIEWER"}).status_code == 200
    assert client.patch(f"/api/v1/users/{other.id}", json={"is_active": False}).status_code == 200


def test_reviewers_cannot_manage_users(session_factory, tmp_path: Path) -> None:
    app = _app(session_factory, tmp_path)
    with session_factory() as db:
        staff = create_user(db, email="staff@example.com", password_hash="x", role="REVIEWER")
        db.commit()
    client = _as(app, staff)

    assert client.get("/api/v1/users").status_code == 403
    assert (
        client.post(
            "/api/v1/users", json={"email": "a@example.com", "name": "A", "role": "ADMIN", "password": "12345678"}
        ).status_code
        == 403
    )
    assert client.patch(f"/api/v1/users/{staff.id}", json={"role": "ADMIN"}).status_code == 403
