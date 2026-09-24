import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_fake_admin_user() -> object:
    """A non-persisted User standing in for auth.dependencies.require_user's
    return value. Route tests that aren't specifically exercising login/
    role-gating (see test_auth_routes.py for those) override require_user
    with this, so they keep exercising route behavior as an authenticated
    admin, unaffected by the auth/ package's addition."""
    from marriage_ocr_api.auth.models import User
    from marriage_ocr_api.auth.status import Role

    return User(id=uuid4(), number=1, name="Test Admin", email="test-admin@example.com", role=Role.ADMIN.value)
