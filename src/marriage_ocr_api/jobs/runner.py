from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_CLI_COMMAND_BY_DOCUMENT_TYPE = {
    DocumentType.HANDWRITTEN_REGISTER: "process",
    DocumentType.TYPED_BORANG_4B: "process-typed",
}


@dataclass(frozen=True)
class OCRRunRequest:
    input_path: Path
    output_path: Path
    debug_path: Path
    stdout_log_path: Path
    stderr_log_path: Path
    document_type: DocumentType = DocumentType.HANDWRITTEN_REGISTER


@dataclass(frozen=True)
class OCRRunResult:
    return_code: int
    timed_out: bool
    duration_seconds: float


class PopenFactory(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> subprocess.Popen: ...


def sanitize_stderr_text(text: str, limit: int) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def read_sanitized_stderr(stderr_path: Path, limit: int) -> str:
    if not stderr_path.exists():
        return ""
    return sanitize_stderr_text(stderr_path.read_text(encoding="utf-8", errors="ignore"), limit)


def failure_code_for_run(result: OCRRunResult, output_path: Path) -> str | None:
    if result.timed_out:
        return "OCR_PROCESS_TIMEOUT"
    if result.return_code != 0:
        return "OCR_PROCESS_FAILED"
    if not output_path.is_file() or output_path.stat().st_size == 0:
        return "OCR_OUTPUT_MISSING"
    return None


class SubprocessOCRRunner:
    def __init__(
        self,
        settings: Settings,
        popen: PopenFactory | None = None,
        working_directory: Path | None = None,
    ) -> None:
        self.settings = settings
        self._popen = popen or subprocess.Popen
        self._working_directory = working_directory

    def _config_path_for(self, document_type: DocumentType) -> Path:
        if document_type == DocumentType.TYPED_BORANG_4B:
            return self.settings.ocr_config_path_typed
        return self.settings.ocr_config_path_handwritten

    def _build_command(self, request: OCRRunRequest) -> list[str]:
        cli_command = _CLI_COMMAND_BY_DOCUMENT_TYPE[request.document_type]
        config_path = self._config_path_for(request.document_type)
        return [
            str(self.settings.ocr_python_executable),
            "-m",
            self.settings.ocr_module,
            cli_command,
            "--input",
            str(request.input_path),
            "--output",
            str(request.output_path),
            "--debug",
            str(request.debug_path),
            "--config",
            str(config_path),
            "--reset-output",
        ]

    def _working_dir(self) -> Path:
        if self.settings.ocr_module.startswith("tests.") and Path("/app").exists():
            return Path("/app")
        if self._working_directory is not None:
            return self._working_directory
        upstream_checkout = Path("/opt/marriage-ocr")
        if upstream_checkout.exists():
            return upstream_checkout
        return Path.cwd()

    def _terminate_process_group(self, process: subprocess.Popen) -> None:
        pid = getattr(process, "pid", None)
        if pid is not None and os.name != "nt":
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        terminate = getattr(process, "terminate", None)
        if callable(terminate):
            terminate()

    def _kill_process_group(self, process: subprocess.Popen) -> None:
        pid = getattr(process, "pid", None)
        if pid is not None and os.name != "nt":
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()

    def run(self, request: OCRRunRequest) -> OCRRunResult:
        request.stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.debug_path.mkdir(parents=True, exist_ok=True)

        command = self._build_command(request)
        env = os.environ.copy()
        source_root = Path(__file__).resolve().parents[3]
        app_root = Path("/app")
        pythonpath = env.get("PYTHONPATH")
        path_entries = [str(app_root)]
        if source_root != app_root:
            path_entries.append(str(source_root))
        if pythonpath:
            path_entries.append(pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(path_entries)
        start = time.monotonic()
        with request.stdout_log_path.open("wb") as stdout_log, request.stderr_log_path.open("wb") as stderr_log:
            process = self._popen(
                command,
                shell=False,
                cwd=str(self._working_dir()),
                env=env,
                stdout=stdout_log,
                stderr=stderr_log,
                start_new_session=True,
            )
            timed_out = False
            return_code = 0
            try:
                return_code = int(process.wait(timeout=self.settings.ocr_timeout_seconds))
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_group(process)
                try:
                    return_code = int(process.wait(timeout=5))
                except Exception:
                    self._kill_process_group(process)
                    try:
                        return_code = int(process.wait(timeout=5))
                    except Exception:
                        return_code = -1
            duration = time.monotonic() - start
        return OCRRunResult(return_code=return_code, timed_out=timed_out, duration_seconds=duration)
