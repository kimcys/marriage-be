"""Removing a worker's local working copies once object storage holds the
real ones.

Under STORAGE_BACKEND=s3 every document input, split page input and job
output is uploaded to Spaces as it's written (and read back from there on
demand -- see jobs/processing.py::_ensure_input_materialized and the
presigned-download responses), so the files a worker leaves on its own disk
afterwards are copies nothing reads again. Kept, they grow without bound:
worker-1's 78GB disk filled with them. Never used for STORAGE_BACKEND=local,
where the local files ARE the originals.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.models import OCRJob

logger = logging.getLogger(__name__)


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def local_roots_for_job(settings: Settings, job: OCRJob) -> list[Path]:
    """The local directories holding this job's working copies: its own job
    folder (batches/<b>/documents/<document>/ for a whole-document job,
    jobs/<job>/ for a split page), plus its Gemini batch folder. Only a
    folder named after this exact document/job is ever returned."""
    storage_root = settings.storage_root.resolve()
    parts = Path(job.input_relative_path).parts
    roots: list[Path] = []
    if len(parts) >= 5 and parts[0] == "batches" and parts[2] == "documents" and parts[3] == str(job.document_id):
        roots.append(storage_root / "batches" / parts[1] / "documents" / parts[3])
    elif len(parts) >= 3 and parts[0] == "jobs" and parts[1] == str(job.id):
        roots.append(storage_root / "jobs" / parts[1])
    roots.append(storage_root / "gemini-batch" / str(job.id))
    return [root for root in roots if _within(storage_root, root) and root != storage_root]


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def remove_local_copies_for_job(settings: Settings, job: OCRJob) -> int:
    """Best-effort; returns bytes freed. A no-op unless STORAGE_BACKEND=s3."""
    if settings.storage_backend != "s3":
        return 0
    freed = 0
    for root in local_roots_for_job(settings, job):
        if root.exists():
            size = directory_size(root)
            shutil.rmtree(root, ignore_errors=True)
            freed += size
    return freed


def remove_local_file_copy(settings: Settings, path: Path) -> None:
    """Drop one already-uploaded local file (e.g. a document's full PDF once
    it has been split into per-page inputs). A no-op unless s3."""
    if settings.storage_backend != "s3" or not _within(settings.storage_root, path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove local copy %s", path, exc_info=True)
