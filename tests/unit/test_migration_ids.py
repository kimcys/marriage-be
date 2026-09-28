import re
from pathlib import Path


def test_migration_revision_ids_fit_alembic_version_column() -> None:
    """alembic_version.version_num is VARCHAR(32): a longer revision id fails
    on Postgres (production) while SQLite silently accepts it."""
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    too_long = {}
    for path in versions.glob("*.py"):
        match = re.search(r'^revision = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
        if match and len(match.group(1)) > 32:
            too_long[path.name] = match.group(1)
    assert too_long == {}
