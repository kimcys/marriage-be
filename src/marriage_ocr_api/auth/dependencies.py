from __future__ import annotations

from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from marriage_ocr_api.api.dependencies import get_db_session, settings_dependency
from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.auth import repositories
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.security import decode_access_token
from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.core.config import Settings

# auto_error=False: a missing/malformed Authorization header should 401
# through our own ApiError envelope (same {error: {code, message,
# request_id}} shape every other failure uses), not FastAPI's default
# "Not authenticated" 403 in a different shape.
_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = ApiError(401, "UNAUTHENTICATED", "A valid Authorization: Bearer token is required.")


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(settings_dependency),
    session: Session = Depends(get_db_session),
) -> User:
    if credentials is None:
        raise _UNAUTHENTICATED
    try:
        payload = decode_access_token(settings, credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _UNAUTHENTICATED from exc

    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _UNAUTHENTICATED from exc

    user = repositories.get_user(session, user_id)
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != Role.ADMIN.value:
        raise ApiError(403, "FORBIDDEN", "This action requires the admin role.")
    return user
