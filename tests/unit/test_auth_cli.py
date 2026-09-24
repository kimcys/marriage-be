from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.auth import cli
from marriage_ocr_api.auth.repositories import create_user
from marriage_ocr_api.batches.models import Batch
from marriage_ocr_api.batches.repositories import create_batch
from marriage_ocr_api.db.base import Base


@pytest.fixture
def session_factory(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "get_session_factory", lambda _settings: factory)
    return factory


def test_assign_batches_only_fills_unassigned_by_default(session_factory, capsys) -> None:
    with session_factory() as session:
        owner = create_user(session, email="owner@example.com", password_hash="x", role="ADMIN")
        other = create_user(session, email="other@example.com", password_hash="x", role="REVIEWER")
        unassigned = create_batch(session, name="Old", description=None, created_by=None)
        assigned = create_batch(session, name="Theirs", description=None, created_by=other.id)
        session.commit()

    assert (owner.number, other.number) == (1, 2)
    cli._assign_batches("1", include_assigned=False)

    with session_factory() as session:
        assert session.get(Batch, unassigned.id).created_by == owner.id
        assert session.get(Batch, assigned.id).created_by == other.id
    assert "1 batch(es)" in capsys.readouterr().out

    cli._assign_batches("owner@example.com", include_assigned=True)  # email works too
    with session_factory() as session:
        assert session.get(Batch, assigned.id).created_by == owner.id


def test_assign_batches_rejects_unknown_email(session_factory) -> None:
    with pytest.raises(SystemExit):
        cli._assign_batches("nobody@example.com", include_assigned=False)
    with pytest.raises(SystemExit):
        cli._assign_batches("#7", include_assigned=False)
