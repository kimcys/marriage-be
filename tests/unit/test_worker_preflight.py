import os
from pathlib import Path

import pytest

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.preflight import WorkerPreflightError, check_google_credentials


def _settings(path: Path) -> Settings:
    return Settings(google_application_credentials=str(path))


def test_accepts_readable_json_credentials(tmp_path: Path) -> None:
    creds = tmp_path / "google-vision.json"
    creds.write_text('{"type": "service_account"}')

    check_google_credentials(_settings(creds))


def test_rejects_directory_left_by_missing_bind_mount_source(tmp_path: Path) -> None:
    creds = tmp_path / "google-vision.json"
    creds.mkdir()

    with pytest.raises(WorkerPreflightError, match="is a directory"):
        check_google_credentials(_settings(creds))


def test_rejects_missing_credentials(tmp_path: Path) -> None:
    with pytest.raises(WorkerPreflightError, match="does not exist"):
        check_google_credentials(_settings(tmp_path / "missing.json"))


def test_rejects_non_json_credentials(tmp_path: Path) -> None:
    creds = tmp_path / "google-vision.json"
    creds.write_text("not json")

    with pytest.raises(WorkerPreflightError, match="not readable JSON"):
        check_google_credentials(_settings(creds))


@pytest.mark.skipif(os.getuid() == 0, reason="root can read any file")
def test_rejects_credentials_unreadable_by_container_user(tmp_path: Path) -> None:
    creds = tmp_path / "google-vision.json"
    creds.write_text('{"type": "service_account"}')
    creds.chmod(0o000)

    with pytest.raises(WorkerPreflightError, match="isn't readable by uid"):
        check_google_credentials(_settings(creds))
