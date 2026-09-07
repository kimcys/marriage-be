from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.api.errors import ApiError
from marriage_ocr_api.batches.repositories import create_batch, list_documents
from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import list_jobs
from marriage_ocr_api.onedrive.repositories import create_submission, get_submission, mark_failed, mark_fetching
from marriage_ocr_api.onedrive.runner import Classification, OneDriveFetchError
from marriage_ocr_api.onedrive.service import recover_stale_submissions, retry_submission, run_onedrive_fetch
from marriage_ocr_api.onedrive.status import OneDriveSubmissionStatus

_FAKE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


def _engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


class FakeJobExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, job_id: UUID) -> None:
        self.submitted.append(job_id)


class FakeFetchRunner:
    def __init__(self, files: dict[str, bytes], classifications: dict[str, Classification]) -> None:
        self._files = files
        self._classifications = classifications
        self.fetch_calls: list[tuple[str, Path]] = []

    def fetch_public(self, url: str, dest: Path) -> None:
        self.fetch_calls.append((url, dest))
        dest.mkdir(parents=True, exist_ok=True)
        for filename, content in self._files.items():
            (dest / filename).write_bytes(content)

    def classify(self, file_path: Path) -> Classification:
        return self._classifications[file_path.name]


class FailingFetchRunner:
    def fetch_public(self, url: str, dest: Path) -> None:
        raise OneDriveFetchError("sign-in required", stderr="requires interactive sign-in")

    def classify(self, file_path: Path) -> Classification:
        raise AssertionError("classify should never be called when fetch itself fails")


def test_run_onedrive_fetch_routes_a_routable_file_and_submits_its_job(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!routable")
    session.commit()
    session.close()

    runner = FakeFetchRunner(
        files={"page1.pdf": _FAKE_PDF_BYTES},
        classifications={
            "page1.pdf": Classification(
                doc_type="handwritten",
                record_type="nikah",
                layout_variant="legacy",
                status="ROUTABLE",
                config_path="config/handwritten.yaml",
            )
        },
    )
    job_executor = FakeJobExecutor()

    run_onedrive_fetch(submission.id, Settings(storage_root=tmp_path), session_factory, job_executor, runner)

    check_session = session_factory()
    updated = get_submission(check_session, submission.id)
    assert updated.status == OneDriveSubmissionStatus.FETCHED.value
    assert updated.skipped_files is None

    documents = list_documents(check_session, batch.id, limit=10, offset=0)
    assert len(documents) == 1
    assert documents[0].document_type == DocumentType.HANDWRITTEN_REGISTER.value
    assert documents[0].onedrive_submission_id == submission.id

    jobs = list_jobs(check_session, None, limit=10, offset=0, document_id=documents[0].id)
    assert len(jobs) == 1
    assert len(job_executor.submitted) == 1
    assert job_executor.submitted[0] == jobs[0].id
    check_session.close()


def test_run_onedrive_fetch_skips_unroutable_files_without_creating_documents(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!jawi")
    session.commit()
    session.close()

    runner = FakeFetchRunner(
        files={"jawi.jpg": b"not-a-real-image"},
        classifications={
            "jawi.jpg": Classification(
                doc_type="handwritten",
                record_type="nikah",
                layout_variant="legacy",
                status="SKIPPED_JAWI",
                config_path=None,
            )
        },
    )
    job_executor = FakeJobExecutor()

    run_onedrive_fetch(submission.id, Settings(storage_root=tmp_path), session_factory, job_executor, runner)

    check_session = session_factory()
    updated = get_submission(check_session, submission.id)
    assert updated.status == OneDriveSubmissionStatus.FETCHED.value
    assert updated.skipped_files == [{"filename": "jawi.jpg", "status": "SKIPPED_JAWI"}]
    assert list_documents(check_session, batch.id, limit=10, offset=0) == []
    assert job_executor.submitted == []
    check_session.close()


def test_run_onedrive_fetch_marks_submission_failed_when_fetch_itself_fails(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!signin-required")
    session.commit()
    session.close()

    run_onedrive_fetch(
        submission.id, Settings(storage_root=tmp_path), session_factory, FakeJobExecutor(), FailingFetchRunner()
    )

    check_session = session_factory()
    updated = get_submission(check_session, submission.id)
    assert updated.status == OneDriveSubmissionStatus.FAILED.value
    assert updated.error_code == "ONEDRIVE_FETCH_FAILED"
    assert "sign-in" in (updated.error_message or "")
    check_session.close()


def test_run_onedrive_fetch_summarizes_a_long_rich_traceback_stderr(tmp_path: Path) -> None:
    """error_message is a String(1000) column. marriage-ocr's CLI renders an
    uncaught failure as a full Rich traceback panel on stderr -- thousands of
    characters of box-drawing art and ANSI escapes -- which SQLite (used by
    this test) won't reject but a real Postgres column would, failing the
    UPDATE and leaving the submission stuck at FETCHING with no visible
    error at all. Rich always follows the panel with one plain "ExceptionType:
    message" line; that's what should end up stored, not the panel."""

    # Rich wraps a long summary line across the console width, so the real
    # final "ExceptionType: message" text can itself span several physical
    # lines -- not just one -- after the panel's bottom border.
    box_traceback = (
        "\x1b[31m╭──────── Traceback ────────╮\x1b[0m\n"
        + ("\x1b[31m│\x1b[0m " + ("x" * 200) + "\n") * 20
        + "\x1b[31m╰───────────────────────────╯\x1b[0m\n"
        "RuntimeError: OneDrive rendered a page that isn't a recognizable\n"
        "file or folder listing.\n"
    )

    class LongTracebackFetchRunner:
        def fetch_public(self, url: str, dest: Path) -> None:
            raise OneDriveFetchError("onedrive fetch-public exited with code 1", stderr=box_traceback)

        def classify(self, file_path: Path) -> Classification:
            raise AssertionError("classify should never be called when fetch itself fails")

    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!long-error")
    session.commit()
    session.close()

    run_onedrive_fetch(
        submission.id,
        Settings(storage_root=tmp_path),
        session_factory,
        FakeJobExecutor(),
        LongTracebackFetchRunner(),
    )

    check_session = session_factory()
    updated = get_submission(check_session, submission.id)
    assert updated.status == OneDriveSubmissionStatus.FAILED.value
    assert len(box_traceback) > 1000, "the fixture must actually exceed the column width to test truncation"
    assert updated.error_message == (
        "RuntimeError: OneDrive rendered a page that isn't a recognizable file or folder listing."
    )
    check_session.close()


class FakeOneDriveExecutor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    def submit(self, submission_id: UUID) -> None:
        self.submitted.append(submission_id)


class FailingOneDriveExecutor:
    def submit(self, submission_id: UUID) -> None:
        raise RuntimeError("queue is down")


def test_recover_stale_submissions_fails_only_ones_past_the_cutoff() -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    stale = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!stale")
    fresh = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!fresh")
    mark_fetching(session, stale.id)
    mark_fetching(session, fresh.id)
    stale_row = get_submission(session, stale.id)
    stale_row.updated_at = datetime.now(UTC) - timedelta(seconds=7200)
    session.commit()

    recovered_count = recover_stale_submissions(session, stale_after_seconds=3600)
    session.commit()

    assert recovered_count == 1
    assert get_submission(session, stale.id).status == OneDriveSubmissionStatus.FAILED.value
    assert get_submission(session, fresh.id).status == OneDriveSubmissionStatus.FETCHING.value
    session.close()


def test_retry_submission_resets_and_resubmits_a_failed_submission() -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!retry")
    mark_failed(session, submission.id, error_code="ONEDRIVE_FETCH_FAILED", error_message="sign-in required")
    session.commit()

    executor = FakeOneDriveExecutor()
    retried = retry_submission(session, submission.id, executor)
    session.commit()

    assert retried.status == OneDriveSubmissionStatus.PENDING.value
    assert retried.error_message is None
    assert executor.submitted == [submission.id]
    session.close()


def test_retry_submission_rejects_a_submission_that_is_not_failed() -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!not-failed")
    session.commit()

    with pytest.raises(ApiError):
        retry_submission(session, submission.id, FakeOneDriveExecutor())
    session.close()


def test_retry_submission_marks_failed_again_when_resubmission_itself_fails() -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!retry-fails")
    mark_failed(session, submission.id, error_code="ONEDRIVE_FETCH_FAILED", error_message="sign-in required")
    session.commit()

    with pytest.raises(ApiError):
        retry_submission(session, submission.id, FailingOneDriveExecutor())

    updated = get_submission(session, submission.id)
    assert updated.status == OneDriveSubmissionStatus.FAILED.value
    assert updated.error_code == "INTERNAL_PROCESSING_ERROR"
    session.close()
