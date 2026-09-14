from __future__ import annotations

from sqlalchemy.orm import Session

from marriage_ocr_api.auth import repositories
from marriage_ocr_api.auth.models import User
from marriage_ocr_api.auth.security import encode_access_token, verify_password
from marriage_ocr_api.core.config import Settings


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    user = repositories.get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def issue_access_token(settings: Settings, user: User) -> str:
    return encode_access_token(settings, user_id=user.id, email=user.email, role=user.role)
