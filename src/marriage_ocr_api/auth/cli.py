"""Create/update local user accounts. There's no self-registration surface
by design -- this is a 2-person internal tool, so accounts are created
directly by whoever runs it, never through a public signup flow.

Lives under src/ (not scripts/, which the Dockerfile never copies into the
image) specifically so it's runnable inside the running container:

    docker compose exec api python -m marriage_ocr_api.auth.cli create-user \\
        --email admin@example.com --role ADMIN

Also `list-users` (every account's id/email/role) and `assign-batches`
(set the "added by" user on batches that predate it being recorded).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from marriage_ocr_api.auth import repositories
from marriage_ocr_api.auth.models import USER_CODE_PREFIX, User
from marriage_ocr_api.auth.security import hash_password
from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.batches.models import Batch
from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory


def _create_user(email: str, role: str, name: str | None) -> None:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        raise SystemExit(1)

    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        existing = repositories.get_user_by_email(session, email)
        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.role = role
            if name is not None:
                existing.name = name
            session.commit()
            print(f"Updated existing user {existing.code} {email} (role={role}).")
            return
        user = repositories.create_user(
            session, email=email, password_hash=hash_password(password), role=role, name=name
        )
        session.commit()
        print(f"Created user {user.code} {email} (role={role}).")


def _find_user(session: Session, user_ref: str) -> User | None:
    """A user by code (MOCR001), number (1 / #1) or email."""
    ref = user_ref.strip()
    number = ref.upper().removeprefix(USER_CODE_PREFIX).removeprefix("#")
    if number.isdigit():
        return session.scalar(select(User).where(User.number == int(number)))
    return repositories.get_user_by_email(session, ref)


def _set_name(user_ref: str, name: str) -> None:
    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        user = _find_user(session, user_ref)
        if user is None:
            print(f"No user {user_ref}. Run list-users to see accounts.", file=sys.stderr)
            raise SystemExit(1)
        user.name = name.strip() or None
        session.commit()
        print(f"{user.code} ({user.email}) is now named {user.name!r}.")


def _list_users() -> None:
    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        for user in session.scalars(select(User).order_by(User.created_at)):
            print(f"{user.code}  {user.name or '-'}  {user.email}  {user.role}")


def _assign_batches(user_ref: str, include_assigned: bool) -> None:
    """Sets batches.created_by to the user `user_ref` names -- their code
    (MOCR001), number or email -- only on batches with no creator recorded
    yet, unless `include_assigned` (then every batch)."""
    session_factory = get_session_factory(get_settings())
    with session_factory() as session:
        user = _find_user(session, user_ref)
        if user is None:
            print(f"No user {user_ref}. Run list-users to see accounts.", file=sys.stderr)
            raise SystemExit(1)
        stmt = update(Batch).values(created_by=user.id)
        if not include_assigned:
            stmt = stmt.where(Batch.created_by.is_(None))
        result = cast(CursorResult[Any], session.execute(stmt))
        session.commit()
        print(f"Set {user.code} ({user.email}) as the creator of {result.rowcount} batch(es).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-user", help="Create a new user, or reset an existing one's password/role."
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--role", required=True, choices=[role.value for role in Role])
    create_parser.add_argument("--name", help="The person's name, shown when hovering over their code.")

    name_parser = subparsers.add_parser("set-name", help="Set a user's display name.")
    name_parser.add_argument("--user", required=True, help="The user's code (MOCR001), number or email.")
    name_parser.add_argument("--name", required=True)

    subparsers.add_parser("list-users", help="Print every user's code, name, email and role.")

    assign_parser = subparsers.add_parser(
        "assign-batches", help='Set the "added by" user on batches that have none recorded yet.'
    )
    assign_parser.add_argument("--user", required=True, help="The user's code (MOCR001), number or email.")
    assign_parser.add_argument(
        "--include-assigned",
        action="store_true",
        help="Also overwrite batches that already have a creator (default: only unassigned ones).",
    )

    args = parser.parse_args()
    if args.command == "create-user":
        _create_user(args.email, args.role, args.name)
    elif args.command == "set-name":
        _set_name(args.user, args.name)
    elif args.command == "list-users":
        _list_users()
    elif args.command == "assign-batches":
        _assign_batches(args.user, args.include_assigned)


if __name__ == "__main__":
    main()
