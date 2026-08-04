from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.core.config import Settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "Marriage OCR API"}


class ReadinessChecks(BaseModel):
    database: str
    storage: str
    ocr_config: str
    ocr_python: str


class ReadinessResponse(BaseModel):
    status: str
    checks: ReadinessChecks


@router.get("/ready", response_model=ReadinessResponse)
def ready(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> JSONResponse:
    checks = {
        "database": "failed",
        "storage": "failed",
        "ocr_config": "failed",
        "ocr_python": "failed",
    }

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        pass

    storage_root = settings.storage_root
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        if storage_root.is_dir() and os.access(storage_root, os.W_OK):
            checks["storage"] = "ok"
    except Exception:
        pass

    if settings.ocr_config_path.is_file():
        checks["ocr_config"] = "ok"

    if settings.ocr_python_executable.is_file() and os.access(settings.ocr_python_executable, os.X_OK):
        checks["ocr_python"] = "ok"

    status = 200 if all(value == "ok" for value in checks.values()) else 503
    body: dict[str, object] = {"status": "ready" if status == 200 else "not ready", "checks": checks}
    return JSONResponse(status_code=status, content=body)
