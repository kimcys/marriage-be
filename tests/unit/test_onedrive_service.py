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
from marriage_ocr_api.onedrive.repositories import (
    create_submission,
    get_submission,
    mark_failed,
    mark_fetched,
    mark_fetching,
)
from marriage_ocr_api.onedrive.runner import Classification, OneDriveFetchError
from marriage_ocr_api.onedrive.service import (
    _apply_neighbor_fallback,
    _ingest_one_file,
    classify_skipped_file,
    delete_submission,
    recover_stale_submissions,
    retry_submission,
    run_onedrive_fetch,
)
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


def test_ingest_one_file_success_removes_the_staged_source(tmp_path: Path) -> None:
    """Once the Document/Job rows are durably committed, the OneDrive-staged
    original is redundant -- and must be removed so a future submission
    retry's re-scan of the download directory doesn't find it again and
    ingest (duplicate) it a second time."""
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!ok")
    session.commit()

    source_path = tmp_path / "downloaded" / "page1.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_FAKE_PDF_BYTES)

    _ingest_one_file(
        session,
        Settings(storage_root=tmp_path),
        FakeJobExecutor(),
        batch_id=batch.id,
        submission_id=submission.id,
        source_path=source_path,
        document_type=DocumentType.HANDWRITTEN_REGISTER,
    )

    assert not source_path.exists()
    assert len(list_documents(session, batch.id, limit=10, offset=0)) == 1
    session.close()


def test_ingest_one_file_failure_preserves_the_staged_source_for_retry(tmp_path: Path, monkeypatch) -> None:
    """A file that fails partway through ingestion (DB error, page-split
    failure, etc.) must not be silently, permanently lost -- the original
    OneDrive-staged copy has to survive so a submission retry can find and
    re-attempt it. This is what local_intake.save_local_file copying
    (rather than moving) the source, combined with _ingest_one_file only
    deleting it after a successful commit, is meant to guarantee."""
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!flaky")
    session.commit()

    source_path = tmp_path / "downloaded" / "page1.pdf"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_FAKE_PDF_BYTES)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated transient DB failure")

    monkeypatch.setattr("marriage_ocr_api.onedrive.service.create_document", _boom)

    with pytest.raises(RuntimeError, match="simulated transient DB failure"):
        _ingest_one_file(
            session,
            Settings(storage_root=tmp_path),
            FakeJobExecutor(),
            batch_id=batch.id,
            submission_id=submission.id,
            source_path=source_path,
            document_type=DocumentType.HANDWRITTEN_REGISTER,
        )

    assert source_path.exists()
    assert source_path.read_bytes() == _FAKE_PDF_BYTES
    assert list_documents(session, batch.id, limit=10, offset=0) == []
    session.close()


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
    expected_storage_key = f"onedrive/{submission.id}/skipped/jawi.jpg"
    assert updated.skipped_files == [
        {"filename": "jawi.jpg", "status": "SKIPPED_JAWI", "storage_key": expected_storage_key}
    ]
    # The bytes must actually survive run_onedrive_fetch's final
    # shutil.rmtree of the download directory -- that's the whole point of
    # moving them out to a sibling "skipped" directory instead of leaving
    # them in "downloaded".
    assert (tmp_path / expected_storage_key).read_bytes() == b"not-a-real-image"
    assert list_documents(check_session, batch.id, limit=10, offset=0) == []
    assert job_executor.submitted == []
    check_session.close()


_ROUTABLE_NIKAH_LEGACY = Classification(
    doc_type="handwritten",
    record_type="nikah",
    layout_variant="legacy",
    status="ROUTABLE",
    config_path="config/handwritten.yaml",
)
_ROUTABLE_CERAI_MODERN = Classification(
    doc_type="handwritten",
    record_type="cerai",
    layout_variant="modern",
    status="ROUTABLE",
    config_path="config/handwritten_cerai_modern.yaml",
)
_UNKNOWN = Classification(
    doc_type="unknown", record_type=None, layout_variant=None, status="NEEDS_MANUAL_CLASSIFICATION", config_path=None
)


def test_apply_neighbor_fallback_inherits_when_both_neighbours_agree() -> None:
    # A continuation page whose title band Vision didn't recognise at all --
    # confirmed on a real client batch (a bound ledger book's own middle
    # pages), not just an OCR-garbled title (triage.py's fuzzy match already
    # covers that case).
    classified = [
        (Path("page1.jpg"), _ROUTABLE_NIKAH_LEGACY),
        (Path("page2.jpg"), _UNKNOWN),
        (Path("page3.jpg"), _ROUTABLE_NIKAH_LEGACY),
    ]
    result = _apply_neighbor_fallback(classified)
    assert [c.status for _, c in result] == ["ROUTABLE", "ROUTABLE", "ROUTABLE"]
    inherited = result[1][1]
    assert (inherited.doc_type, inherited.record_type, inherited.layout_variant) == ("handwritten", "nikah", "legacy")
    assert inherited.config_path == "config/handwritten.yaml"


def test_apply_neighbor_fallback_inherits_across_a_run_of_several_unknowns() -> None:
    classified = [
        (Path("page1.jpg"), _ROUTABLE_NIKAH_LEGACY),
        (Path("page2.jpg"), _UNKNOWN),
        (Path("page3.jpg"), _UNKNOWN),
        (Path("page4.jpg"), _UNKNOWN),
        (Path("page5.jpg"), _ROUTABLE_NIKAH_LEGACY),
    ]
    result = _apply_neighbor_fallback(classified)
    assert all(c.status == "ROUTABLE" for _, c in result)


def test_apply_neighbor_fallback_leaves_it_unresolved_when_neighbours_disagree() -> None:
    # A genuine boundary between two different books/record types in the
    # same batch must not guess -- there is no supporting evidence for
    # either side to safely inherit.
    classified = [
        (Path("page1.jpg"), _ROUTABLE_NIKAH_LEGACY),
        (Path("page2.jpg"), _UNKNOWN),
        (Path("page3.jpg"), _ROUTABLE_CERAI_MODERN),
    ]
    result = _apply_neighbor_fallback(classified)
    assert result[1][1].status == "NEEDS_MANUAL_CLASSIFICATION"


def test_apply_neighbor_fallback_leaves_it_unresolved_at_a_batch_edge() -> None:
    # No neighbour at all on one side (start/end of the batch) -- nothing to
    # inherit from.
    classified = [
        (Path("page1.jpg"), _UNKNOWN),
        (Path("page2.jpg"), _ROUTABLE_NIKAH_LEGACY),
    ]
    result = _apply_neighbor_fallback(classified)
    assert result[0][1].status == "NEEDS_MANUAL_CLASSIFICATION"


def test_apply_neighbor_fallback_never_touches_a_confidently_unroutable_status() -> None:
    # SKIPPED_JAWI/BLOCKED_NO_TEMPLATE carry real signal (a genuinely Jawi
    # page, a typed doc_type with no matching template) -- only a bare
    # doc_type="unknown" (no header keyword matched at all) is eligible for
    # fallback.
    jawi = Classification(
        doc_type="handwritten", record_type="nikah", layout_variant="legacy", status="SKIPPED_JAWI", config_path=None
    )
    classified = [
        (Path("page1.jpg"), _ROUTABLE_NIKAH_LEGACY),
        (Path("page2.jpg"), jawi),
        (Path("page3.jpg"), _ROUTABLE_NIKAH_LEGACY),
    ]
    result = _apply_neighbor_fallback(classified)
    assert result[1][1].status == "SKIPPED_JAWI"


def test_run_onedrive_fetch_routes_a_file_via_neighbour_fallback(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!fallback")
    session.commit()
    session.close()

    runner = FakeFetchRunner(
        files={"page1.pdf": _FAKE_PDF_BYTES, "page2.pdf": _FAKE_PDF_BYTES, "page3.pdf": _FAKE_PDF_BYTES},
        classifications={
            "page1.pdf": _ROUTABLE_NIKAH_LEGACY,
            "page2.pdf": _UNKNOWN,
            "page3.pdf": _ROUTABLE_NIKAH_LEGACY,
        },
    )
    job_executor = FakeJobExecutor()

    run_onedrive_fetch(submission.id, Settings(storage_root=tmp_path), session_factory, job_executor, runner)

    check_session = session_factory()
    updated = get_submission(check_session, submission.id)
    assert updated.skipped_files is None
    documents = list_documents(check_session, batch.id, limit=10, offset=0)
    assert len(documents) == 3
    assert all(doc.document_type == DocumentType.HANDWRITTEN_REGISTER.value for doc in documents)
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


def _classification(record_type: str = "nikah") -> Classification:
    return Classification(
        doc_type="handwritten",
        record_type=record_type,
        layout_variant="legacy",
        status="ROUTABLE",
        config_path="config/handwritten.yaml",
    )


def test_delete_submission_removes_only_its_own_documents_jobs_and_files(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission_a = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!delete-a")
    submission_b = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!keep-b")
    session.commit()
    session.close()

    settings = Settings(storage_root=tmp_path)
    run_onedrive_fetch(
        submission_a.id,
        settings,
        session_factory,
        FakeJobExecutor(),
        FakeFetchRunner(files={"a.pdf": _FAKE_PDF_BYTES}, classifications={"a.pdf": _classification()}),
    )
    run_onedrive_fetch(
        submission_b.id,
        settings,
        session_factory,
        FakeJobExecutor(),
        FakeFetchRunner(files={"b.pdf": _FAKE_PDF_BYTES}, classifications={"b.pdf": _classification()}),
    )

    session = session_factory()
    all_documents = list_documents(session, batch.id, limit=10, offset=0)
    doc_a = next(d for d in all_documents if d.onedrive_submission_id == submission_a.id)
    doc_b = next(d for d in all_documents if d.onedrive_submission_id == submission_b.id)
    doc_a_dir = tmp_path / "batches" / str(batch.id) / "documents" / str(doc_a.id)
    doc_b_dir = tmp_path / "batches" / str(batch.id) / "documents" / str(doc_b.id)
    assert doc_a_dir.exists()
    assert doc_b_dir.exists()

    delete_submission(session, settings, submission_a.id)
    session.commit()

    assert get_submission(session, submission_a.id) is None
    assert get_submission(session, submission_b.id) is not None
    remaining_documents = list_documents(session, batch.id, limit=10, offset=0)
    assert [d.id for d in remaining_documents] == [doc_b.id]
    remaining_jobs = list_jobs(session, None, limit=10, offset=0, document_id=doc_a.id)
    assert remaining_jobs == []
    assert not doc_a_dir.exists()
    assert doc_b_dir.exists()
    session.close()


def test_classify_skipped_file_ingests_it_as_a_normal_job(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!manual")
    session.commit()
    session.close()

    settings = Settings(storage_root=tmp_path)
    runner = FakeFetchRunner(
        files={"image00001.pdf": _FAKE_PDF_BYTES},
        classifications={"image00001.pdf": _UNKNOWN},
    )
    run_onedrive_fetch(submission.id, settings, session_factory, FakeJobExecutor(), runner)

    session = session_factory()
    fetched = get_submission(session, submission.id)
    assert fetched.skipped_files is not None and len(fetched.skipped_files) == 1

    job_executor = FakeJobExecutor()
    result = classify_skipped_file(
        session,
        settings,
        job_executor,
        submission.id,
        filename="image00001.pdf",
        document_type=DocumentType.HANDWRITTEN_REGISTER,
    )

    assert result.skipped_files is None
    documents = list_documents(session, batch.id, limit=10, offset=0)
    assert len(documents) == 1
    assert documents[0].document_type == DocumentType.HANDWRITTEN_REGISTER.value
    assert len(job_executor.submitted) == 1
    session.close()


def test_classify_skipped_file_raises_when_submission_missing(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    with pytest.raises(ApiError):
        classify_skipped_file(
            session,
            Settings(storage_root=tmp_path),
            FakeJobExecutor(),
            UUID("00000000-0000-0000-0000-000000000000"),
            filename="image00001.jpg",
            document_type=DocumentType.HANDWRITTEN_REGISTER,
        )
    session.close()


def test_classify_skipped_file_raises_when_filename_not_skipped(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!no-such-file")
    session.commit()

    with pytest.raises(ApiError):
        classify_skipped_file(
            session,
            Settings(storage_root=tmp_path),
            FakeJobExecutor(),
            submission.id,
            filename="does-not-exist.jpg",
            document_type=DocumentType.HANDWRITTEN_REGISTER,
        )
    session.close()


def test_classify_skipped_file_raises_when_bytes_no_longer_available(tmp_path: Path) -> None:
    """A skipped_files entry recorded before this feature existed (or one
    whose preservation move itself failed) has no storage_key -- nothing
    left to classify."""
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    batch = create_batch(session, name="Batch 1", description=None, created_by=None)
    submission = create_submission(session, batch_id=batch.id, url="https://1drv.ms/f/s!legacy-skip")
    mark_fetched(session, submission.id, skipped_files=[{"filename": "old.jpg", "status": "SKIPPED_JAWI"}])
    session.commit()

    with pytest.raises(ApiError):
        classify_skipped_file(
            session,
            Settings(storage_root=tmp_path),
            FakeJobExecutor(),
            submission.id,
            filename="old.jpg",
            document_type=DocumentType.HANDWRITTEN_REGISTER,
        )
    session.close()


def test_delete_submission_for_missing_submission_raises(tmp_path: Path) -> None:
    engine = _engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    with pytest.raises(ApiError):
        delete_submission(session, Settings(storage_root=tmp_path), UUID("00000000-0000-0000-0000-000000000000"))
    session.close()
