from __future__ import annotations

import json
import os
from pathlib import Path

from marriage_ocr_api.core.config import Settings


class WorkerPreflightError(RuntimeError):
    pass


def check_google_credentials(settings: Settings) -> None:
    """Fail worker startup if the Google Vision service-account file isn't a
    readable JSON file. Every OCR and classify subprocess needs it, and when
    the host-side bind-mount source is missing Docker silently creates an
    empty *directory* at /run/secrets/google-vision.json instead of failing
    -- the worker then starts fine and marks every file CLASSIFY_FAILED.
    Crashing here makes that misconfiguration visible at deploy time."""
    path = Path(settings.google_application_credentials)
    if path.is_dir():
        raise WorkerPreflightError(
            f"GOOGLE_APPLICATION_CREDENTIALS ({path}) is a directory, not a file -- the host path "
            "bind-mounted onto it probably doesn't exist on this Droplet (Docker creates an empty "
            "directory in that case). Copy google-vision.json to the host path set in .env.production."
        )
    if not path.is_file():
        raise WorkerPreflightError(f"GOOGLE_APPLICATION_CREDENTIALS ({path}) does not exist")
    try:
        raw = path.read_text(encoding="utf-8")
    except PermissionError as exc:
        raise WorkerPreflightError(
            f"GOOGLE_APPLICATION_CREDENTIALS ({path}) isn't readable by uid {os.getuid()} -- the container "
            "runs as non-root `app` (uid 10001), so on the host run `chown 10001:10001 <file> && chmod 400 <file>`."
        ) from exc
    try:
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise WorkerPreflightError(f"GOOGLE_APPLICATION_CREDENTIALS ({path}) is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkerPreflightError(f"GOOGLE_APPLICATION_CREDENTIALS ({path}) is not a JSON object")
