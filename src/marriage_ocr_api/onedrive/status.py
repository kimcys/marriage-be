from __future__ import annotations

from enum import StrEnum


class OneDriveSubmissionStatus(StrEnum):
    PENDING = "PENDING"
    FETCHING = "FETCHING"
    FETCHED = "FETCHED"
    FAILED = "FAILED"
