from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_docker_only_end_to_end_smoke() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "scripts/docker_e2e.py"], cwd=repo_root, check=False)
    assert result.returncode == 0
