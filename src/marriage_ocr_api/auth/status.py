from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    REVIEWER = "REVIEWER"
    # Read-only: can browse and download but every write endpoint refuses them.
    VIEWER = "VIEWER"
