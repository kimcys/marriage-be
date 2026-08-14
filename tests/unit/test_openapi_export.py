from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_export_openapi_script_writes_file(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    artifacts_path = repo_root / "artifacts" / "openapi.json"
    if artifacts_path.exists():
        artifacts_path.unlink()
    result = subprocess.run([sys.executable, "scripts/export_openapi.py"], cwd=repo_root, check=False)
    assert result.returncode == 0
    assert artifacts_path.exists()
