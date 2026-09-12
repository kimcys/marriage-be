from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from uuid import UUID

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.api.dependencies import get_db_session
from marriage_ocr_api.batches.repositories import get_document, list_documents
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import get_job
from marriage_ocr_api.jobs.executor import JobExecutor
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.main import create_app
from marriage_ocr_api.onedrive.repositories import create_submission
from marriage_ocr_api.onedrive.runner import Classification
from marriage_ocr_api.onedrive.service import run_onedrive_fetch
from marriage_ocr_api.records.repositories import get_record

_NIKAH_CLASSIFICATION = Classification(
    doc_type="handwritten",
    record_type="nikah",
    layout_variant="legacy",
    status="ROUTABLE",
    config_path="config/handwritten.yaml",
)
# Typed Nikah now splits legacy/modern the same way Cerai/Rujuk already do
# (marriage-ocr's `classify` never reports layout_variant=None for it any
# more) -- see CLASSIFICATION_TO_DOCUMENT_TYPE, which no longer has a
# ("typed", "nikah", None) entry.
_BORANG_4B_CLASSIFICATION = Classification(
    doc_type="typed",
    record_type="nikah",
    layout_variant="modern",
    status="ROUTABLE",
    config_path="config/typed_nikah_modern.yaml",
)


class _FakeFetchRunner:
    """Stands in for a real OneDrive fetch: hands back a local test PDF
    instead of hitting the network, so these tests exercise the real
    page-splitting/job-creation pipeline (onedrive/service.py) without
    depending on marriage-ocr's own onedrive fetch-public/classify
    subprocesses."""

    def __init__(self, source_pdf: Path, filename: str, classification: Classification) -> None:
        self._source_pdf = source_pdf
        self._filename = filename
        self._classification = classification

    def fetch_public(self, url: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._source_pdf, dest / self._filename)

    def classify(self, file_path: Path) -> Classification:
        return self._classification


def _make_pdf(path: Path, page_count: int) -> None:
    document = pymupdf.open()
    for index in range(page_count):
        page = document.new_page()
        page.insert_text((72, 72), f"page {index + 1}")
    document.save(path)
    document.close()


def _settings(tmp_path: Path) -> Settings:
    config_path = tmp_path / "config" / "production.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("ok: true\n", encoding="utf-8")
    database_url = f"sqlite+pysqlite:///{tmp_path / 'app.db'}"
    return Settings(
        storage_root=tmp_path / "storage",
        database_url=database_url,
        ocr_python_executable=Path(sys.executable),
        ocr_module="tests.fixtures.fake_ocr_cli",
        ocr_config_path_handwritten=config_path,
        ocr_config_path_typed=config_path,
        ocr_config_dir=config_path.parent,
    )


def _client(settings: Settings) -> tuple[TestClient, sessionmaker[Session]]:
    # File-based SQLite (not :memory:), so a real connection pool -- not
    # StaticPool's single shared connection -- lets the background executor
    # thread and this test's polling loop query concurrently without racing
    # on the same cursor.
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app(settings)
    app.state.session_factory = session_factory
    app.state.executor = JobExecutor(settings, session_factory, SubprocessOCRRunner(settings))

    def override_session() -> Session:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_session
    return TestClient(app), session_factory


def _wait_for_document_status(session_factory: sessionmaker[Session], document_id: UUID, expected: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with session_factory() as session:
            document = get_document(session, document_id)
            if document is not None and document.status == expected:
                return
        time.sleep(0.1)
    raise AssertionError(f"document did not reach {expected} in time")


def _ingest_via_onedrive(
    client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    batch_id: UUID,
    source_pdf: Path,
    filename: str,
    classification: Classification,
) -> UUID:
    with session_factory() as session:
        submission = create_submission(session, batch_id=batch_id, url=f"https://1drv.ms/f/s!{filename}")
        session.commit()
        submission_id = submission.id

    runner = _FakeFetchRunner(source_pdf, filename, classification)
    run_onedrive_fetch(submission_id, settings, session_factory, client.app.state.executor, runner)

    with session_factory() as session:
        documents = list_documents(session, batch_id, limit=10, offset=0)
        assert len(documents) == 1, "expected exactly one document created from the onedrive submission"
        return documents[0].id


@pytest.mark.integration
def test_multipage_handwritten_pdf_is_split_into_per_page_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "success")
    settings = _settings(tmp_path)
    client, session_factory = _client(settings)

    source_pdf = tmp_path / "register.pdf"
    _make_pdf(source_pdf, 3)

    try:
        batch_response = client.post("/api/v1/batches", json={"name": "Split batch"})
        assert batch_response.status_code == 201
        batch_id = UUID(batch_response.json()["id"])

        document_id = _ingest_via_onedrive(
            client,
            session_factory,
            settings,
            batch_id=batch_id,
            source_pdf=source_pdf,
            filename="register.pdf",
            classification=_NIKAH_CLASSIFICATION,
        )
        with session_factory() as session:
            assert get_document(session, document_id).page_count == 3

        _wait_for_document_status(session_factory, document_id, "PROCESSED")

        jobs_response = client.get("/api/v1/jobs", params={"document_id": str(document_id), "limit": 100})
        assert jobs_response.status_code == 200
        jobs = jobs_response.json()["items"]
        assert len(jobs) == 3
        assert [job["page_number"] for job in jobs] == [1, 2, 3]
        assert all(job["status"] == "COMPLETED" for job in jobs)

        export_response = client.post(
            "/api/v1/exports",
            json={"batch_id": str(batch_id), "format": "XLSX", "include_unreviewed": True},
        )
        assert export_response.status_code == 202

        records_response = client.get("/api/v1/records", params={"limit": 100})
        assert records_response.status_code == 200
        records = records_response.json()["items"]
        # Each of the 3 page-jobs runs the fake CLI's "success" mode, which
        # always emits 2 fixed records -- so 3 pages x 2 records = 6.
        assert len(records) == 6

        # source_page isn't in the API response; verify the override actually
        # took effect (each split job's own OCR output always reports page 1,
        # which would be wrong for pages 2 and 3 without the override) directly
        # against the stored record.
        with session_factory() as session:
            stored_pages = sorted(get_record(session, UUID(record["id"])).source_page for record in records)
        assert stored_pages == [1, 1, 2, 2, 3, 3]
    finally:
        client.app.state.executor.shutdown()


@pytest.mark.integration
def test_multipage_typed_pdf_is_not_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "success")
    settings = _settings(tmp_path)
    client, session_factory = _client(settings)

    source_pdf = tmp_path / "borang4b.pdf"
    _make_pdf(source_pdf, 2)

    try:
        batch_response = client.post("/api/v1/batches", json={"name": "Typed batch"})
        batch_id = UUID(batch_response.json()["id"])

        document_id = _ingest_via_onedrive(
            client,
            session_factory,
            settings,
            batch_id=batch_id,
            source_pdf=source_pdf,
            filename="borang4b.pdf",
            classification=_BORANG_4B_CLASSIFICATION,
        )
        with session_factory() as session:
            assert get_document(session, document_id).page_count is None

        _wait_for_document_status(session_factory, document_id, "PROCESSED")

        jobs_response = client.get("/api/v1/jobs", params={"document_id": str(document_id), "limit": 100})
        jobs = jobs_response.json()["items"]
        assert len(jobs) == 1
        assert jobs[0]["page_number"] is None
    finally:
        client.app.state.executor.shutdown()


@pytest.mark.integration
def test_one_failed_page_still_lets_document_process_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "fail-named-input")
    monkeypatch.setenv("FAKE_OCR_FAIL_INPUT_NAME", "page-2.pdf")
    settings = _settings(tmp_path)
    client, session_factory = _client(settings)

    source_pdf = tmp_path / "register.pdf"
    _make_pdf(source_pdf, 3)

    try:
        batch_response = client.post("/api/v1/batches", json={"name": "Partial failure batch"})
        batch_id = UUID(batch_response.json()["id"])

        document_id = _ingest_via_onedrive(
            client,
            session_factory,
            settings,
            batch_id=batch_id,
            source_pdf=source_pdf,
            filename="register.pdf",
            classification=_NIKAH_CLASSIFICATION,
        )

        # Even with one page permanently failing (for now), the document should
        # still reach PROCESSED once the other two pages finish -- not get stuck.
        _wait_for_document_status(session_factory, document_id, "PROCESSED")

        jobs_response = client.get(
            "/api/v1/jobs", params={"document_id": str(document_id), "status": "FAILED", "limit": 100}
        )
        failed_jobs = jobs_response.json()["items"]
        assert len(failed_jobs) == 1
        failed_job_id = failed_jobs[0]["id"]
        assert failed_jobs[0]["page_number"] == 2

        # Fix the underlying cause and retry just the failed page.
        monkeypatch.setenv("FAKE_OCR_MODE", "success")
        retry_response = client.post(f"/api/v1/jobs/{failed_job_id}/retry")
        assert retry_response.status_code == 200

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with session_factory() as session:
                job = get_job(session, UUID(failed_job_id))
                if job is not None and job.status == "COMPLETED":
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("retried job did not complete in time")
    finally:
        client.app.state.executor.shutdown()
