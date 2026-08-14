from __future__ import annotations

import logging

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.exports.service import cleanup_stale_exports
from marriage_ocr_api.jobs.celery_app import celery_app

logger = logging.getLogger(__name__)

# Every export request writes a brand-new file with no expiry (see
# exports/service.py::cleanup_stale_exports) -- unbounded storage growth at
# 1M-record scale otherwise. Generous enough that nobody loses an export
# they still need; short enough that storage doesn't grow forever.
EXPORT_RETENTION_DAYS = 30.0


@celery_app.task(name="marriage_ocr_api.exports.cleanup_stale_exports", bind=False)
def cleanup_stale_exports_task() -> int:
    settings = get_settings()
    session_factory = get_session_factory(settings)
    with session_factory() as session:
        deleted = cleanup_stale_exports(session, settings, EXPORT_RETENTION_DAYS)
    if deleted:
        logger.info("Deleted %s export(s) older than %s days", deleted, EXPORT_RETENTION_DAYS)
    return deleted
