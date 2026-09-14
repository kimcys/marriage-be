"""Create/update local user accounts. There's no self-registration surface
by design -- this is a 2-person internal tool, so accounts are created
directly by whoever runs it, never through a public signup flow.

Lives under src/ (not scripts/, which the Dockerfile never copies into the
image) specifically so it's runnable inside the running container:

    docker compose exec api python -m marriage_ocr_api.auth.cli create-user \\
        --email admin@example.com --role ADMIN
"""
from __future__ import annotations

import argparse
import getpass
import sys

from marriage_ocr_api.auth import repositories
from marriage_ocr_api.auth.security import hash_password
from marriage_ocr_api.auth.status import Role
from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory


def _create_user(email: str, role: str) -> None:
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
            session.commit()
            print(f"Updated existing user {email} (role={role}).")
            return
        repositories.create_user(session, email=email, password_hash=hash_password(password), role=role)
        session.commit()
        print(f"Created user {email} (role={role}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-user", help="Create a new user, or reset an existing one's password/role."
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--role", required=True, choices=[role.value for role in Role])

    args = parser.parse_args()
    if args.command == "create-user":
        _create_user(args.email, args.role)


if __name__ == "__main__":
    main()
