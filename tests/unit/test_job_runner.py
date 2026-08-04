from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.runner import (
    OCRRunRequest,
    OCRRunResult,
    SubprocessOCRRunner,
    failure_code_for_run,
    sanitize_stderr_text,
)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    config_path = tmp_path / "config" / "production.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("ok: true\n", encoding="utf-8")
    data = {
        "storage_root": tmp_path,
        "ocr_python_executable": Path(sys.executable),
        "ocr_module": "tests.fixtures.fake_ocr_cli",
        "ocr_config_path": config_path,
    }
    data.update(overrides)
    return Settings(**data)


def test_runner_builds_expected_command_and_shell_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = OCRRunRequest(
        input_path=tmp_path / "input.pdf",
        output_path=tmp_path / "output.xlsx",
        debug_path=tmp_path / "debug",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )
    request.input_path.write_bytes(b"%PDF-1.4\n")

    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            request.output_path.write_bytes(b"fake-xlsx")
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("marriage_ocr_api.jobs.runner.subprocess.Popen", fake_popen)

    runner = SubprocessOCRRunner(settings)
    result = runner.run(request)

    assert isinstance(result, OCRRunResult)
    assert captured["kwargs"]["shell"] is False
    assert captured["args"] == [
        str(Path(sys.executable)),
        "-m",
        "tests.fixtures.fake_ocr_cli",
        "process",
        "--input",
        str(request.input_path),
        "--output",
        str(request.output_path),
        "--debug",
        str(request.debug_path),
        "--config",
        str(settings.ocr_config_path),
        "--reset-output",
    ]


def test_runner_reports_missing_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = OCRRunRequest(
        input_path=tmp_path / "input.pdf",
        output_path=tmp_path / "output.xlsx",
        debug_path=tmp_path / "debug",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )
    request.input_path.write_bytes(b"%PDF-1.4\n")

    class FakeProcess:
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(args, **kwargs):
        return FakeProcess()

    runner = SubprocessOCRRunner(settings, popen=fake_popen)
    result = runner.run(request)

    assert result.return_code == 0
    assert not result.timed_out
    assert failure_code_for_run(result, request.output_path) == "OCR_OUTPUT_MISSING"


def test_runner_reports_process_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = OCRRunRequest(
        input_path=tmp_path / "input.pdf",
        output_path=tmp_path / "output.xlsx",
        debug_path=tmp_path / "debug",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )
    request.input_path.write_bytes(b"%PDF-1.4\n")

    class FakeProcess:
        returncode = 1

        def wait(self, timeout: float | None = None) -> int:
            return 1

    runner = SubprocessOCRRunner(settings, popen=lambda *args, **kwargs: FakeProcess())
    result = runner.run(request)

    assert result.return_code == 1
    assert failure_code_for_run(result, request.output_path) == "OCR_PROCESS_FAILED"


def test_runner_reports_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = OCRRunRequest(
        input_path=tmp_path / "input.pdf",
        output_path=tmp_path / "output.xlsx",
        debug_path=tmp_path / "debug",
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )
    request.input_path.write_bytes(b"%PDF-1.4\n")

    calls: list[str] = []

    class FakeProcess:
        pid = 123
        returncode = None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    def fake_popen(args, **kwargs):
        return FakeProcess()

    runner = SubprocessOCRRunner(settings, popen=fake_popen)
    result = runner.run(request)

    assert result.timed_out is True
    assert "terminate" in calls
    assert "kill" in calls


def test_sanitizes_ansi_sequences_from_stderr() -> None:
    assert sanitize_stderr_text("\x1b[31mfailed\x1b[0m\n", 100) == "failed\n"
