from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


def _run(command: list[str], *, cwd: Path, env: dict[str, str], input_text: str | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True, input=input_text, text=input_text is not None)


def _create_test_admin_and_login(client: httpx.Client, repo_root: Path, env: dict[str, str]) -> None:
    email = "docker-e2e@example.com"
    password = "docker-e2e-password"
    _run(
        ["/usr/bin/env", "docker", "compose", "exec", "-T", "api", "python", "-m", "marriage_ocr_api.auth.cli"]
        + ["create-user", "--email", email, "--role", "ADMIN"],
        cwd=repo_root,
        env=env,
        input_text=f"{password}\n{password}\n",
    )
    login_response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    login_response.raise_for_status()
    token = login_response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"


def _wait_for_ready(client: httpx.Client, timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200:
                payload = response.json()
                if payload.get("status") == "ready":
                    return payload
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"API never became ready: {last_error!r}")


def _wait_for_onedrive_submission_fetched(
    client: httpx.Client, batch_id: str, submission_id: str, timeout_seconds: int = 120
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/batches/{batch_id}/onedrive-links")
        response.raise_for_status()
        for item in response.json().get("items", []):
            if item["id"] != submission_id:
                continue
            if item["status"] == "FETCHED":
                return item
            if item["status"] == "FAILED":
                raise RuntimeError(f"onedrive submission failed: {item!r}")
        time.sleep(2)
    raise RuntimeError("onedrive submission did not reach FETCHED in time")


def _wait_for_job_completion(client: httpx.Client, timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get("/api/v1/jobs")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        if items:
            job = items[0]
            if job.get("status") == "COMPLETED":
                return job
            if job.get("status") == "FAILED":
                raise RuntimeError(f"OCR job failed: {job!r}")
        time.sleep(2)
    raise RuntimeError("OCR job did not complete in time")


def _wait_for_records(client: httpx.Client, timeout_seconds: int = 120) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get("/api/v1/records")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        if items:
            return items
        time.sleep(2)
    raise RuntimeError("records did not appear in time")


def _wait_for_export(client: httpx.Client, export_id: str, timeout_seconds: int = 60) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/exports/{export_id}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "COMPLETED":
            return payload
        if payload.get("status") == "FAILED":
            raise RuntimeError(f"export failed: {payload!r}")
        time.sleep(1)
    raise RuntimeError("export did not complete in time")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="marriage-be-e2e-") as tmpdir:
        google_creds = Path(tmpdir) / "fake-google.json"
        google_creds.write_text("{}", encoding="utf-8")
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(google_creds)
        env["OCR_MODULE"] = "tests.fixtures.fake_ocr_cli"
        env["OCR_PYTHON_EXECUTABLE"] = "/opt/venv/bin/python"
        env["OCR_CONFIG_PATH_HANDWRITTEN"] = "/opt/marriage-ocr/config/production.yaml"
        env["OCR_CONFIG_PATH_TYPED"] = "/opt/marriage-ocr/config/typed_borang4b.yaml"
        env["GEMINI_API_KEY"] = ""
        try:
            _run(
                ["/usr/bin/env", "docker", "compose", "up", "-d", "--build", "postgres", "valkey", "api", "worker"],
                cwd=repo_root,
                env=env,
            )
            with httpx.Client(base_url="http://localhost:8000", timeout=10.0) as client:
                _wait_for_ready(client)
                _create_test_admin_and_login(client, repo_root, env)

                batch_response = client.post(
                    "/api/v1/batches",
                    json={"name": "Docker E2E Batch", "description": "smoke test"},
                )
                batch_response.raise_for_status()
                batch_id = batch_response.json()["id"]

                # Real ingestion is OneDrive-link-only (see README's "Job
                # Execution" section) -- there's no direct-upload endpoint
                # any more. The fake OCR CLI's `onedrive fetch-public` and
                # `classify` handlers (tests/fixtures/fake_ocr_cli.py) fake
                # out the two subprocess calls this needs, so a placeholder
                # URL works with no real OneDrive access from CI.
                submission_response = client.post(
                    f"/api/v1/batches/{batch_id}/onedrive-links",
                    json={"url": "https://1drv.ms/f/s!docker-e2e-fake-link"},
                )
                submission_response.raise_for_status()
                submission_id = submission_response.json()["id"]
                _wait_for_onedrive_submission_fetched(client, batch_id, submission_id)

                job = _wait_for_job_completion(client)
                assert job["status"] == "COMPLETED"

                records = _wait_for_records(client)
                record_ids = [record["id"] for record in records]
                approve_response = client.post(
                    "/api/v1/records/bulk-approve",
                    json={"record_ids": record_ids, "reviewer": "docker-e2e@example.com"},
                )
                approve_response.raise_for_status()

                export_response = client.post(
                    "/api/v1/exports",
                    json={"batch_id": batch_id, "format": "XLSX", "include_unreviewed": False},
                )
                export_response.raise_for_status()
                export_id = export_response.json()["id"]
                export = _wait_for_export(client, export_id)

                download_response = client.get(f"/api/v1/exports/{export_id}/download")
                download_response.raise_for_status()
                assert download_response.content
                assert export["status"] == "COMPLETED"
                print(
                    json.dumps(
                        {
                            "batch_id": batch_id,
                            "job_id": job["id"],
                            "records": len(records),
                            "export_id": export_id,
                            "download_bytes": len(download_response.content),
                        }
                    )
                )
        finally:
            subprocess.run(["/usr/bin/env", "docker", "compose", "down", "-v"], cwd=repo_root, env=env, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
