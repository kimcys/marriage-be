from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from marriage_ocr_api.batches.models import Document
from marriage_ocr_api.batches.pdf_splitter import pdf_page_count, split_pdf_into_pages
from marriage_ocr_api.batches.status import HANDWRITTEN_DOCUMENT_TYPES, DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.paths import JobPaths, build_job_paths
from marriage_ocr_api.jobs.status import JobStatus


def document_paths(storage_root: Path, batch_id: UUID, document_id: UUID, extension: str = ".pdf") -> JobPaths:
    resolved_root = storage_root.resolve()
    document_root = (resolved_root / "batches" / str(batch_id) / "documents" / str(document_id)).resolve()
    if not document_root.is_relative_to(resolved_root):
        raise ValueError("generated document paths must remain inside STORAGE_ROOT")
    input_dir = document_root / "input"
    output_dir = document_root / "output"
    debug_dir = document_root / "debug"
    logs_dir = document_root / "logs"
    normalized = extension.lower()
    return JobPaths(
        storage_root=resolved_root,
        job_root=document_root,
        input_dir=input_dir,
        output_dir=output_dir,
        debug_dir=debug_dir,
        logs_dir=logs_dir,
        input_source_path=input_dir / f"source{normalized}",
        input_part_path=input_dir / f"source{normalized}.part",
        output_result_path=output_dir / "result.xlsx",
        stdout_log_path=logs_dir / "stdout.log",
        stderr_log_path=logs_dir / "stderr.log",
    )


def split_page_count(document_type: DocumentType, media_type: str, input_path: Path) -> int | None:
    """Return the page count if this document should be split into per-page jobs.

    Only handwritten PDFs with more than one page are split -- every typed
    record type spans a fixed page count handled inside process-typed
    itself (e.g. two physical pages per logical Borang 4B record), so
    splitting those would sever a record in half.
    """
    if document_type not in HANDWRITTEN_DOCUMENT_TYPES or media_type != "application/pdf":
        return None
    page_count = pdf_page_count(input_path)
    return page_count if page_count > 1 else None


def create_document_page_jobs(
    session: Session,
    settings: Settings,
    *,
    batch_id: UUID,
    document: Document,
    input_path: Path,
    page_count: int,
    document_type: DocumentType,
) -> list[UUID]:
    staging_dir = input_path.parent / "pages"
    page_paths = split_pdf_into_pages(input_path, staging_dir)
    job_ids: list[UUID] = []
    # Each page-job's files live under storage_root/jobs/{job_id}/, entirely
    # outside the document's own directory tree. If job creation fails
    # partway through a multi-page PDF (DB error, disk pressure), the
    # caller's cleanup only removes the document's own directory -- these
    # already-created per-page job directories would otherwise be silently
    # orphaned on disk forever. Track and remove them here too.
    created_job_roots: list[Path] = []
    try:
        for page_number, page_source_path in enumerate(page_paths, start=1):
            page_job_id = uuid4()
            job_paths = build_job_paths(settings.storage_root, page_job_id, ".pdf")
            created_job_roots.append(job_paths.job_root)
            job_paths.input_dir.mkdir(parents=True, exist_ok=True)
            # Keep the "page-N.pdf" name (rather than job_paths' generic "source.pdf")
            # so the input file stays traceable to its original page on disk.
            input_path = job_paths.input_dir / page_source_path.name
            shutil.move(str(page_source_path), input_path)
            input_relative_path = input_path.relative_to(job_paths.storage_root).as_posix()
            create_job(
                session,
                id=page_job_id,
                batch_id=batch_id,
                document_id=document.id,
                status=JobStatus.PENDING,
                document_type=document_type,
                page_number=page_number,
                original_filename=document.original_filename,
                stored_filename=input_path.name,
                content_type="application/pdf",
                file_size_bytes=input_path.stat().st_size,
                input_relative_path=input_relative_path,
                debug_relative_path=job_paths.debug_relative_path,
                stdout_log_relative_path=job_paths.stdout_log_relative_path,
                stderr_log_relative_path=job_paths.stderr_log_relative_path,
                ocr_git_ref=settings.marriage_ocr_git_ref,
            )
            job_ids.append(page_job_id)
    except Exception:
        for job_root in created_job_roots:
            shutil.rmtree(job_root, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return job_ids
