from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from marriage_ocr_api.api.errors import ApiError, build_error_response
from marriage_ocr_api.api.routers.health import router as health_router
from marriage_ocr_api.api.routers.jobs import router as jobs_router
from marriage_ocr_api.core.config import Settings, get_settings
from marriage_ocr_api.core.logging import configure_logging
from marriage_ocr_api.core.request_id import (
    RequestIdMiddleware,
    normalize_request_id,
    request_id_context,
)
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.executor import JobExecutor
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.jobs.service import recover_interrupted_jobs
from marriage_ocr_api.storage.local import UploadValidationError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    session_factory = get_session_factory(settings)
    app.state.session_factory = session_factory
    app.state.executor = JobExecutor(settings, session_factory, SubprocessOCRRunner(settings))
    with session_factory() as session:
        recover_interrupted_jobs(session)
        session.commit()
    yield
    app.state.executor.shutdown()


def _error_response(
    status_code: int, code: str, message: str, request_id: str, details: list[str] | None = None
) -> JSONResponse:
    payload = build_error_response(code=code, message=message, request_id=request_id, details=details)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"X-Request-ID": request_id},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.app_log_level)
    app = FastAPI(
        title=settings.app_name,
        description="FastAPI backend for the Marriage OCR pipeline.",
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness endpoints"},
            {"name": "jobs", "description": "OCR job submission and retrieval"},
        ],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.executor = None

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        return _error_response(exc.status_code, exc.code, exc.message, request_id)

    @app.exception_handler(UploadValidationError)
    async def handle_upload_validation_error(request: Request, exc: UploadValidationError) -> JSONResponse:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        return _error_response(exc.status_code, exc.code, exc.message, request_id)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        details = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            details.append(f"{location}: {error.get('msg', 'invalid')}")
        return _error_response(
            422,
            "REQUEST_VALIDATION_ERROR",
            "The request body or parameters are invalid.",
            request_id,
            details or None,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        code = "INTERNAL_ERROR"
        message = "An unexpected error occurred."
        if exc.status_code == 404:
            code = "JOB_NOT_FOUND"
            message = "OCR job not found."
        return _error_response(exc.status_code, code, message, request_id)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_context.get() or normalize_request_id(request.headers.get("X-Request-ID"))
        return _error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.", request_id)

    app.include_router(health_router)
    app.include_router(jobs_router)
    return app


app = create_app()
