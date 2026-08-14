from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

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


def test_batch_migration_creates_documents_and_exports_tables(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'batches.db'}"
    root = Path(__file__).resolve().parents[2]
    _run_alembic("upgrade", database_url, root)

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"batches", "documents", "exports"} <= tables

    _run_alembic("downgrade", database_url, root)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "batches" not in tables
    assert "documents" not in tables
    assert "exports" not in tables
