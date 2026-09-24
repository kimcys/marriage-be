from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marriage_ocr_api.auth.models import User


def get_user(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def create_user(session: Session, *, email: str, password_hash: str, role: str, name: str | None = None) -> User:
    # Next number after the highest in use. Users are only ever created one at
    # a time via auth/cli.py; the column's UNIQUE constraint still rejects a
    # duplicate should two ever race.
    number = (session.scalar(select(func.max(User.number))) or 0) + 1
    user = User(number=number, email=email, password_hash=password_hash, role=role, name=name)
    session.add(user)
    session.flush()
    return user
