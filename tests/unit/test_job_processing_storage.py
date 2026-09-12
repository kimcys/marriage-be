from __future__ import annotations

from pathlib import Path
from uuid import UUID

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job, get_job
from marriage_ocr_api.jobs.processing import process_ocr_job
from marriage_ocr_api.jobs.runner import OCRRunRequest, OCRRunResult
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.records.models import OCRRecord


class FakeSuccessRunner:
    def run(self, request: OCRRunRequest) -> OCRRunResult:
        # A real subprocess needs its input file to exist -- fail loudly
        # (like the real OCR CLI would) if the S3-materialize step didn't
        # actually put it there before the run.
        assert request.input_path.exists(), "input file must be materialized before the OCR run"
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = openpyxl.Workbook()
        workbook.active.append(["full_name", "Confidence"])
        workbook.active.append(["Ada Lovelace", 0.97])
        workbook.save(request.output_path)
        return OCRRunResult(return_code=0, timed_out=False, duration_seconds=0.01)


class FakeTypedSuccessRunner:
    """Stands in for a real `process-typed` CLI run -- always writes CSV
    content, regardless of what extension `request.output_path` happens to
    have (matching every real typed DocumentType, not just TYPED_BORANG_4B;
    see the is_typed fix in jobs/processing.py)."""

    def run(self, request: OCRRunRequest) -> OCRRunResult:
        assert request.input_path.exists(), "input file must be materialized before the OCR run"
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_text(
            "Bil,Nama Suami,Source File,Processing Status\n1,AHMAD BIN ALI,test.pdf,SUCCESS\n",
            encoding="utf-8",
        )
        return OCRRunResult(return_code=0, timed_out=False, duration_seconds=0.01)


class FakeStorageService:
    def __init__(self) -> None:
        self.materialize_calls: list[tuple[str, Path]] = []
        self.put_file_calls: list[tuple[Path, str]] = []

    def materialize(self, key: str, destination: Path) -> Path:
        self.materialize_calls.append((key, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")
        return destination

    def put_file(self, source: Path, key: str):
        self.put_file_calls.append((source, key))


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_pending_job(session_factory, job_id: UUID, *, document_type: DocumentType | None = None) -> None:
    with session_factory() as session:
        kwargs = {} if document_type is None else {"document_type": document_type}
        create_job(
            session,
            id=job_id,
            status=JobStatus.PENDING,
            original_filename="register.pdf",
            stored_filename="source.pdf",
            content_type="application/pdf",
            file_size_bytes=1,
            input_relative_path=f"jobs/{job_id}/input/source.pdf",
            debug_relative_path=f"jobs/{job_id}/debug",
            stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
            stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
            ocr_git_ref="abc123",
            **kwargs,
        )
        session.commit()


def test_process_ocr_job_materializes_missing_input_from_s3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(storage_root=tmp_path, storage_backend="s3")
    session_factory = _session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614176000")
    _seed_pending_job(session_factory, job_id)
    # No file written at the expected local input path -- simulates a worker
    # on a different Droplet than whichever node downloaded/uploaded it.

    fake_storage = FakeStorageService()
    monkeypatch.setattr("marriage_ocr_api.jobs.processing.get_storage_service", lambda settings: fake_storage)

    process_ocr_job(job_id, settings, session_factory, FakeSuccessRunner())

    with session_factory() as session:
        job = get_job(session, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value

    expected_input_path = tmp_path.resolve() / f"jobs/{job_id}/input/source.pdf"
    assert fake_storage.materialize_calls == [(f"jobs/{job_id}/input/source.pdf", expected_input_path)]
    # The completed output must also be pushed up, since a different API
    # instance than this worker may serve the eventual download request.
    assert len(fake_storage.put_file_calls) == 1
    output_source, output_key = fake_storage.put_file_calls[0]
    assert output_key == f"jobs/{job_id}/output/result.xlsx"
    assert output_source.exists()


def test_process_ocr_job_skips_materialize_when_input_already_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(storage_root=tmp_path, storage_backend="s3")
    session_factory = _session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614176001")
    _seed_pending_job(session_factory, job_id)
    input_path = tmp_path / f"jobs/{job_id}/input/source.pdf"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")

    fake_storage = FakeStorageService()
    monkeypatch.setattr("marriage_ocr_api.jobs.processing.get_storage_service", lambda settings: fake_storage)

    process_ocr_job(job_id, settings, session_factory, FakeSuccessRunner())

    assert fake_storage.materialize_calls == []
    # The output is still pushed up regardless of whether the input needed
    # fetching -- it's still a different node's disk than the API's.
    assert len(fake_storage.put_file_calls) == 1


def test_process_ocr_job_never_touches_storage_service_for_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(storage_root=tmp_path, storage_backend="local")
    session_factory = _session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614176002")
    _seed_pending_job(session_factory, job_id)
    input_path = tmp_path / f"jobs/{job_id}/input/source.pdf"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")

    def _fail_if_called(settings):
        raise AssertionError("get_storage_service must not be called for STORAGE_BACKEND=local")

    monkeypatch.setattr("marriage_ocr_api.jobs.processing.get_storage_service", _fail_if_called)

    process_ocr_job(job_id, settings, session_factory, FakeSuccessRunner())

    with session_factory() as session:
        job = get_job(session, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value


@pytest.mark.parametrize(
    "document_type",
    [
        DocumentType.TYPED_BORANG_4B,
        DocumentType.TYPED_NIKAH_LEGACY,
        DocumentType.TYPED_NIKAH_MODERN,
        DocumentType.TYPED_CERAI_LEGACY,
        DocumentType.TYPED_CERAI_MODERN,
        DocumentType.TYPED_RUJUK_LEGACY,
        DocumentType.TYPED_RUJUK_MODERN,
    ],
)
def test_process_ocr_job_uses_csv_output_and_importer_for_every_typed_document_type(
    tmp_path: Path, document_type: DocumentType
) -> None:
    # Regression: `is_typed` used to be `== DocumentType.TYPED_BORANG_4B`,
    # true for only one of these seven values -- every other typed type got
    # `.xlsx` output and import_records_from_xlsx, which would blow up on
    # process-typed's always-CSV content (not a valid XLSX/zip file).
    settings = Settings(storage_root=tmp_path, storage_backend="local")
    session_factory = _session_factory()
    job_id = UUID("123e4567-e89b-12d3-a456-426614176099")
    _seed_pending_job(session_factory, job_id, document_type=document_type)
    input_path = tmp_path / f"jobs/{job_id}/input/source.pdf"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")

    process_ocr_job(job_id, settings, session_factory, FakeTypedSuccessRunner())

    with session_factory() as session:
        job = get_job(session, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED.value
        assert job.output_relative_path is not None
        assert job.output_relative_path.endswith(".csv")
        records = session.query(OCRRecord).filter_by(job_id=job_id).all()
        assert len(records) == 1
        assert records[0].field_values["Nama Suami"] == "AHMAD BIN ALI"
