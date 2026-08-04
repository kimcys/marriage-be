from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class ErrorPayload(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[str] | None = None


class ErrorResponse(BaseModel):
    error: ErrorPayload


@dataclass
class ApiError(Exception):
    status_code: int
    code: str
    message: str


def build_error_response(
    *,
    code: str,
    message: str,
    request_id: str,
    details: list[str] | None = None,
) -> ErrorResponse:
    return ErrorResponse(error=ErrorPayload(code=code, message=message, request_id=request_id, details=details))
