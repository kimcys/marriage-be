from __future__ import annotations

from uuid import UUID

from marriage_ocr_api.onedrive.tasks import fetch_onedrive_submission


class CeleryOneDriveExecutor:
    """Dispatches OneDrive fetch submissions to Celery workers over the
    Valkey broker. See onedrive/executor.py for the thread-pool alternative
    used by default/in tests.
    """

    def submit(self, submission_id: UUID) -> None:
        fetch_onedrive_submission.delay(str(submission_id))

    def shutdown(self) -> None:
        pass
