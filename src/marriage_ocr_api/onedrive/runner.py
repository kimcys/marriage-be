from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from marriage_ocr_api.core.config import Settings


class OneDriveFetchError(RuntimeError):
    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


class ClassifyError(RuntimeError):
    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class Classification:
    doc_type: str
    record_type: str | None
    layout_variant: str | None
    status: str
    config_path: str | None


class OneDriveFetchRunner:
    """Shells out to the vendored /opt/marriage-ocr checkout for the two
    subprocess calls the OneDrive ingestion flow needs: pulling a share link
    down to local disk, then classifying each downloaded file.

    Mirrors jobs/runner.py::SubprocessOCRRunner's working-directory and
    PYTHONPATH handling (same vendored checkout, same test-fixture escape
    hatch) rather than sharing code with it directly -- that class is built
    entirely around one long-running OCRRunRequest with its own timeout/
    kill-process-group semantics, which doesn't fit these two much simpler,
    short-lived, JSON-emitting calls.
    """

    def __init__(self, settings: Settings, working_directory: Path | None = None) -> None:
        self.settings = settings
        self._working_directory = working_directory

    def _working_dir(self) -> Path:
        if self.settings.ocr_module.startswith("tests.") and Path("/app").exists():
            return Path("/app")
        if self._working_directory is not None:
            return self._working_directory
        upstream_checkout = Path("/opt/marriage-ocr")
        if upstream_checkout.exists():
            return upstream_checkout
        return Path.cwd()

    def _env(self) -> dict[str, str]:
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
        return env

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [str(self.settings.ocr_python_executable), "-m", self.settings.ocr_module, *args]
        return subprocess.run(
            command,
            shell=False,
            cwd=str(self._working_dir()),
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=self.settings.ocr_timeout_seconds,
            check=False,
        )

    def fetch_public(self, url: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        result = self._run(["onedrive", "fetch-public", "--url", url, "--dest", str(dest)])
        if result.returncode != 0:
            raise OneDriveFetchError(
                f"onedrive fetch-public exited with code {result.returncode}", stderr=result.stderr
            )

    def classify(self, file_path: Path) -> Classification:
        result = self._run(["classify", "--input", str(file_path)])
        if result.returncode != 0:
            raise ClassifyError(f"classify exited with code {result.returncode}", stderr=result.stderr)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ClassifyError(f"classify produced non-JSON output: {exc}", stderr=result.stderr) from exc
        return Classification(
            doc_type=payload["doc_type"],
            record_type=payload.get("record_type"),
            layout_variant=payload.get("layout_variant"),
            status=payload["status"],
            config_path=payload.get("config_path"),
        )
