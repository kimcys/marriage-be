from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


def _run_alembic(command: str, database_url: str, workdir: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, "head" if command == "upgrade" else "base"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_and_downgrade_migrations(tmp_path: Path) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for migration integration tests")
    if not make_url(database_url).drivername.startswith("postgresql"):
        pytest.skip("migration test requires PostgreSQL")

    root = Path(__file__).resolve().parents[2]
    _run_alembic("upgrade", database_url, root)

    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "ocr_jobs" in inspector.get_table_names()
    index_names = {index["name"] for index in inspector.get_indexes("ocr_jobs")}
    assert {"ix_ocr_jobs_status", "ix_ocr_jobs_created_at"} <= index_names

    _run_alembic("downgrade", database_url, root)
    inspector = inspect(engine)
    assert "ocr_jobs" not in inspector.get_table_names()
