from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.repositories import create_batch, create_document
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.maintenance import clean_local_copies
from marriage_ocr_api.storage.local_copies import local_roots_for_job, remove_local_copies_for_job


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _touch(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def _document_job(session, tmp_path: Path, *, status=JobStatus.COMPLETED, page: bool = False):
    batch = create_batch(session, name="B", description=None, created_by=None)
    document_id = uuid4()
    doc_key = f"batches/{batch.id}/documents/{document_id}/input/source.pdf"
    document = create_document(
        session, id=document_id, batch_id=batch.id, original_filename="a.pdf", safe_filename="source.pdf",
        media_type="application/pdf", size_bytes=10, sha256="0" * 64, storage_key=doc_key,
        document_type=DocumentType.HANDWRITTEN_REGISTER, page_count=None,
    )  # fmt: skip
    job_id = uuid4()
    if page:
        root = f"jobs/{job_id}"
        input_key = f"{root}/input/page-1.pdf"
    else:
        root = f"batches/{batch.id}/documents/{document_id}"
        input_key = doc_key
    job = repositories.create_job(
        session, id=job_id, batch_id=batch.id, document_id=document.id, status=status,
        original_filename="a.pdf", stored_filename="source.pdf", content_type="application/pdf",
        file_size_bytes=10, input_relative_path=input_key, debug_relative_path=f"{root}/debug",
        stdout_log_relative_path=f"{root}/logs/stdout.log", stderr_log_relative_path=f"{root}/logs/stderr.log",
        ocr_git_ref="x", output_relative_path=f"{root}/output/result.xlsx" if status == JobStatus.COMPLETED else None,
        started_at=datetime.now(UTC),
    )  # fmt: skip
    session.commit()
    for relative in (
        input_key,
        f"{root}/output/result.xlsx",
        f"{root}/debug/page/original.jpg",
        f"{root}/logs/stderr.log",
    ):
        _touch(tmp_path / relative)
    return document, job


def test_a_completed_jobs_local_folder_is_removed_only_under_s3(session_factory, tmp_path) -> None:
    session = session_factory()
    _, job = _document_job(session, tmp_path)
    root = local_roots_for_job(Settings(storage_root=tmp_path, storage_backend="s3"), job)[0]

    assert remove_local_copies_for_job(Settings(storage_root=tmp_path, storage_backend="local"), job) == 0
    assert root.exists()  # local storage: these ARE the originals

    freed = remove_local_copies_for_job(Settings(storage_root=tmp_path, storage_backend="s3"), job)
    assert freed > 0 and not root.exists()


def test_a_split_page_job_removes_only_its_own_folder(session_factory, tmp_path) -> None:
    session = session_factory()
    document, job = _document_job(session, tmp_path, page=True)
    settings = Settings(storage_root=tmp_path, storage_backend="s3")
    other = tmp_path / "jobs" / str(uuid4()) / "input" / "page-2.pdf"
    _touch(other)

    remove_local_copies_for_job(settings, job)

    assert not (tmp_path / "jobs" / str(job.id)).exists()
    assert other.exists()  # another page's job folder is untouched


def test_a_folder_not_named_after_the_job_is_never_touched(tmp_path) -> None:
    job = OCRJob(
        id=uuid4(), document_id=uuid4(), input_relative_path="batches/b/documents/SOMEONE-ELSE/input/source.pdf",
        status="COMPLETED",
    )  # fmt: skip
    roots = local_roots_for_job(Settings(storage_root=tmp_path, storage_backend="s3"), job)
    assert all("SOMEONE-ELSE" not in str(root) for root in roots)


class FakeStorage:
    def __init__(self, present: set[str]) -> None:
        self.present = present

    def exists(self, key: str) -> bool:
        return key in self.present


def test_cleanup_only_takes_documents_fully_completed_and_confirmed_in_storage(session_factory, tmp_path) -> None:
    session = session_factory()
    settings = Settings(storage_root=tmp_path, storage_backend="s3")
    done, done_job = _document_job(session, tmp_path)
    running, _ = _document_job(session, tmp_path, status=JobStatus.PROCESSING)
    unconfirmed, unconfirmed_job = _document_job(session, tmp_path)
    storage = FakeStorage(
        {done.storage_key, done_job.output_relative_path, running.storage_key, unconfirmed.storage_key}
    )  # unconfirmed's OUTPUT isn't in storage

    removable, skipped = clean_local_copies.plan(settings, session, storage)

    assert [document_id for document_id, _ in removable] == [str(done.id)]
    assert skipped == {"not every job completed": 1, "an output not in object storage": 1}


def test_cleanup_refuses_without_object_storage(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(clean_local_copies, "get_settings", lambda: Settings(storage_root=tmp_path))
    assert clean_local_copies.main([]) == 2
    assert "Refusing" in capsys.readouterr().err
