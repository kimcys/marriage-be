from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from marriage_ocr_api.auth.models import User


def get_user(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def create_user(session: Session, *, email: str, password_hash: str, role: str) -> User:
    user = User(email=email, password_hash=password_hash, role=role)
    session.add(user)
    session.flush()
    return user
