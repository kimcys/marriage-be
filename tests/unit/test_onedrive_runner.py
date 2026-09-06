from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.onedrive.runner import ClassifyError, OneDriveFetchError, OneDriveFetchRunner


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    data = {
        "storage_root": tmp_path,
        "ocr_python_executable": Path(sys.executable),
        "ocr_module": "tests.fixtures.fake_ocr_cli",
    }
    data.update(overrides)
    return Settings(**data)


def test_fetch_public_builds_expected_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("marriage_ocr_api.onedrive.runner.subprocess.run", fake_run)

    runner = OneDriveFetchRunner(_settings(tmp_path))
    dest = tmp_path / "downloaded"
    runner.fetch_public("https://1drv.ms/f/s!abc", dest)

    assert captured["command"] == [
        str(Path(sys.executable)),
        "-m",
        "tests.fixtures.fake_ocr_cli",
        "onedrive",
        "fetch-public",
        "--url",
        "https://1drv.ms/f/s!abc",
        "--dest",
        str(dest),
    ]
    assert captured["kwargs"]["shell"] is False
    assert dest.is_dir()


def test_fetch_public_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="sign-in required")

    monkeypatch.setattr("marriage_ocr_api.onedrive.runner.subprocess.run", fake_run)

    runner = OneDriveFetchRunner(_settings(tmp_path))
    with pytest.raises(OneDriveFetchError) as excinfo:
        runner.fetch_public("https://1drv.ms/f/s!abc", tmp_path / "downloaded")
    assert excinfo.value.stderr == "sign-in required"


def test_classify_parses_json_stdout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = {
        "doc_type": "handwritten",
        "record_type": "cerai",
        "layout_variant": "legacy",
        "status": "ROUTABLE",
        "config_path": "config/handwritten_cerai_legacy.yaml",
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("marriage_ocr_api.onedrive.runner.subprocess.run", fake_run)

    runner = OneDriveFetchRunner(_settings(tmp_path))
    classification = runner.classify(tmp_path / "page.jpg")

    assert classification.doc_type == "handwritten"
    assert classification.record_type == "cerai"
    assert classification.layout_variant == "legacy"
    assert classification.status == "ROUTABLE"
    assert classification.config_path == "config/handwritten_cerai_legacy.yaml"


def test_classify_raises_on_non_json_stdout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("marriage_ocr_api.onedrive.runner.subprocess.run", fake_run)

    runner = OneDriveFetchRunner(_settings(tmp_path))
    with pytest.raises(ClassifyError):
        runner.classify(tmp_path / "page.jpg")


def test_classify_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("marriage_ocr_api.onedrive.runner.subprocess.run", fake_run)

    runner = OneDriveFetchRunner(_settings(tmp_path))
    with pytest.raises(ClassifyError) as excinfo:
        runner.classify(tmp_path / "page.jpg")
    assert excinfo.value.stderr == "boom"
