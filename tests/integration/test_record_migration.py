from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{Path.cwd() / 'tmp-records.db'}")
    return config


def test_record_migration_creates_and_drops_review_tables(tmp_path: Path) -> None:
    config = _alembic_config()
    database_url = f"sqlite+pysqlite:///{tmp_path / 'records.db'}"
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "ocr_jobs" in inspector.get_table_names()
    assert "ocr_records" in inspector.get_table_names()
    assert "record_revisions" in inspector.get_table_names()

    command.downgrade(config, "base")
    inspector = inspect(engine)
    assert "ocr_jobs" not in inspector.get_table_names()
    assert "ocr_records" not in inspector.get_table_names()
    assert "record_revisions" not in inspector.get_table_names()
