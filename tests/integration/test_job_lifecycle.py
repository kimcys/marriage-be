from __future__ import annotations

import sys
from pathlib import Path

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.runner import (
    OCRRunRequest,
    SubprocessOCRRunner,
    failure_code_for_run,
    read_sanitized_stderr,
)


def _settings(tmp_path: Path) -> Settings:
    config_path = tmp_path / "config" / "production.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("ok: true\n", encoding="utf-8")
    return Settings(
        storage_root=tmp_path,
        ocr_python_executable=Path(sys.executable),
        ocr_module="tests.fixtures.fake_ocr_cli",
        ocr_config_path=config_path,
    )


def _request(tmp_path: Path) -> OCRRunRequest:
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.xlsx"
    debug_path = tmp_path / "debug"
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    input_path.write_bytes(b"%PDF-1.4\n")
    return OCRRunRequest(
        input_path=input_path,
        output_path=output_path,
        debug_path=debug_path,
        stdout_log_path=stdout_log,
        stderr_log_path=stderr_log,
    )


def test_fake_cli_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "success")
    runner = SubprocessOCRRunner(_settings(tmp_path))
    request = _request(tmp_path)

    result = runner.run(request)

    assert result.return_code == 0
    assert failure_code_for_run(result, request.output_path) is None
    assert request.output_path.is_file()
    assert request.output_path.stat().st_size > 0


def test_fake_cli_failure_sanitizes_stderr(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "failure")
    runner = SubprocessOCRRunner(_settings(tmp_path))
    request = _request(tmp_path)

    result = runner.run(request)

    assert result.return_code == 1
    assert failure_code_for_run(result, request.output_path) == "OCR_PROCESS_FAILED"
    assert read_sanitized_stderr(request.stderr_log_path, 100) == "fake ocr failed\n"


def test_fake_cli_no_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "no-output")
    runner = SubprocessOCRRunner(_settings(tmp_path))
    request = _request(tmp_path)

    result = runner.run(request)

    assert result.return_code == 0
    assert failure_code_for_run(result, request.output_path) == "OCR_OUTPUT_MISSING"
