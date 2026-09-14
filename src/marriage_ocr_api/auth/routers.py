from __future__ import annotations

from fastapi import Depends
from fastapi.routing import APIRouter
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth.schemas import LoginRequest, TokenResponse
from marriage_ocr_api.auth.service import authenticate, issue_access_token
from marriage_ocr_api.core.config import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    operation_id="login",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "login": {
                            "summary": "Log in",
                            "value": {"email": "admin@example.com", "password": "change-me"},
                        }
                    }
                }
            }
        }
    },
)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(settings_dependency),
) -> TokenResponse:
    user = authenticate(session, email=payload.email, password=payload.password)
    if user is None:
        raise ApiError(401, "INVALID_CREDENTIALS", "Incorrect email or password.")
    token = issue_access_token(settings, user)
    return TokenResponse(access_token=token, role=user.role)
