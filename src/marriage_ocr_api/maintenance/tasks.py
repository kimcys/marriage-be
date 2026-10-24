from __future__ import annotations

import logging
import threading

from celery.worker.control import control_command

from marriage_ocr_api.core.config import get_settings
from marriage_ocr_api.db.session import get_session_factory
from marriage_ocr_api.jobs.celery_app import celery_app
from marriage_ocr_api.maintenance import clean_local_copies
from marriage_ocr_api.storage.factory import get_storage_service

logger = logging.getLogger(__name__)

# Every worker droplet has its own disk, and a normal task is consumed by
# whichever single worker is free, so a plain beat task would clean one disk
# and leave the others filling (worker-1 hit 100% and failed every job that
# landed on it). Beat therefore broadcasts a control command that each worker
# runs against its own disk.
CLEAN_COMMAND = "clean_local_copies_now"

_running = threading.Lock()


def clean_this_workers_disk() -> None:
    # The scan can take a long time on a disk with a backlog; a second
    # broadcast arriving meanwhile must not start a competing one.
    if not _running.acquire(blocking=False):
        logger.info("local copy cleanup already running on this worker; skipping")
        return
    try:
        settings = get_settings()
        if settings.storage_backend != "s3":
            return
        with get_session_factory(settings)() as session:
            documents, freed, _ = clean_local_copies.clean(settings, session, get_storage_service(settings), apply=True)
        if documents:
            logger.info("removed local copies for %d document(s): %.2f GB", documents, freed / 1e9)
    except Exception:
        logger.exception("local copy cleanup failed")
    finally:
        _running.release()


@control_command()
def clean_local_copies_now(state: object) -> dict[str, str]:
    # Control commands run on the worker's main thread; the cleanup would
    # block heartbeats and every other control command while it works.
    threading.Thread(target=clean_this_workers_disk, name="clean-local-copies", daemon=True).start()
    return {"ok": "started"}


@celery_app.task(name="marriage_ocr_api.maintenance.broadcast_local_cleanup", bind=False)
def broadcast_local_cleanup_task() -> None:
    celery_app.control.broadcast(CLEAN_COMMAND, reply=False)
