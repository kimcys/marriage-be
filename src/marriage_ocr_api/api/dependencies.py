from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from marriage_ocr_api.core.config import Settings, get_settings
from marriage_ocr_api.db.session import iter_session
from marriage_ocr_api.jobs.service import JobExecutorProtocol


def settings_dependency(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


def get_db_session(request: Request) -> Iterator[Session]:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        yield from iter_session(get_settings())
        return

    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_job_executor(request: Request) -> JobExecutorProtocol:
    return cast(JobExecutorProtocol, request.app.state.executor)
