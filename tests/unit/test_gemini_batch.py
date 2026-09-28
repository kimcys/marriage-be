from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db import repositories
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs import gemini_batch, processing
from marriage_ocr_api.jobs.runner import OCRRunResult
from marriage_ocr_api.jobs.status import JobStatus


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(
    session_factory,
    tmp_path: Path,
    *,
    document_type=DocumentType.HANDWRITTEN_REGISTER,
    status=JobStatus.PENDING,
    stage=None,
):
    job_id = uuid4()
    with session_factory() as session:
        repositories.create_job(
            session,
            id=job_id,
            status=status,
            document_type=document_type,
            original_filename="page.jpg",
            stored_filename="source.jpg",
            content_type="image/jpeg",
            file_size_bytes=1,
            input_relative_path=f"jobs/{job_id}/input/source.jpg",
            debug_relative_path=f"jobs/{job_id}/debug",
            stdout_log_relative_path=f"jobs/{job_id}/logs/stdout.log",
            stderr_log_relative_path=f"jobs/{job_id}/logs/stderr.log",
            ocr_git_ref="abc",
            started_at=datetime.now(UTC) if status == JobStatus.PROCESSING else None,
        )
        if stage is not None:
            session.execute(
                update(OCRJob)
                .where(OCRJob.id == job_id)
                .values(gemini_batch_stage=stage, gemini_batch_updated_at=datetime.now(UTC))
            )
        session.commit()
    source = tmp_path / f"jobs/{job_id}/input/source.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    return job_id


def _get(session_factory, job_id: UUID) -> OCRJob:
    with session_factory() as session:
        return session.get(OCRJob, job_id)


class FakeBatchRunner:
    def __init__(self, *, prepare_ok=True, collect_state="JOB_STATE_SUCCEEDED", finish_ok=True, submit_error=None):
        self.prepare_ok, self.collect_state, self.finish_ok, self.submit_error = (
            prepare_ok,
            collect_state,
            finish_ok,
            submit_error,
        )
        self.submitted_items: list[list[dict]] = []
        self.finished: list[str] = []

    def prepare(self, input_path, config_path, out_dir):
        if not self.prepare_ok:
            return False
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "page_1.jpg").write_bytes(b"img")
        (out_dir / "manifest.json").write_text(json.dumps({"pages": [{"index": 1, "image": "page_1.jpg"}]}))
        return True

    def submit(self, items_path, display_name):
        if self.submit_error:
            raise RuntimeError(self.submit_error)
        items = json.loads(items_path.read_text())
        self.submitted_items.append(items)
        return [{"name": "batches/one", "model": "m", "keys": [item["key_prefix"] for item in items]}]

    def collect(self, batch_name, out_dir):
        done = self.collect_state in gemini_batch_terminal()
        return {"state": self.collect_state, "done": done, "ok": [], "errors": {}}

    def finish(self, *, output_path, key_prefix, **kwargs):
        self.finished.append(key_prefix)
        if self.finish_ok:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"xlsx")
        return self.finish_ok


def gemini_batch_terminal():
    return {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}


class FakeOcrRunner:
    def __init__(self):
        self.live_runs: list[Path] = []

    def config_path_for(self, document_type):
        return Path("config/handwritten.yaml")

    def run(self, request, cancel_requested=None):
        self.live_runs.append(request.input_path)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"xlsx")
        return OCRRunResult(return_code=0, timed_out=False, duration_seconds=0.1)


class FakeExecutor:
    def __init__(self):
        self.submitted: list[UUID] = []

    def submit(self, job_id):
        self.submitted.append(job_id)


@pytest.fixture
def completed(monkeypatch):
    """complete_job imports the XLSX; here just record and mark COMPLETED."""
    done: list[UUID] = []

    def fake_complete(settings, session_factory, job, output_path, is_typed):
        with session_factory() as session:
            repositories.mark_completed(session, job.id, str(output_path), datetime.now(UTC))
            session.commit()
        done.append(job.id)

    monkeypatch.setattr(processing, "complete_job", fake_complete)
    return done


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(storage_root=tmp_path, gemini_batch_enabled=True, **overrides)


def test_enabled_handwritten_job_is_prepared_instead_of_run_live(
    session_factory, tmp_path, monkeypatch, completed
) -> None:
    job_id = _job(session_factory, tmp_path)
    batch_runner, ocr_runner = FakeBatchRunner(), FakeOcrRunner()
    monkeypatch.setattr(gemini_batch, "GeminiBatchRunner", lambda settings: batch_runner)

    processing.process_ocr_job(job_id, _settings(tmp_path), session_factory, ocr_runner)

    job = _get(session_factory, job_id)
    assert (job.status, job.gemini_batch_stage) == (JobStatus.PROCESSING.value, gemini_batch.PREPARED)
    assert ocr_runner.live_runs == []


@pytest.mark.parametrize(
    ("enabled", "document_type", "prepare_ok"),
    [
        (False, DocumentType.HANDWRITTEN_REGISTER, True),
        (True, DocumentType.TYPED_NIKAH_MODERN, True),
        (True, DocumentType.HANDWRITTEN_REGISTER, False),
    ],
)
def test_job_runs_live_when_batching_is_off_typed_or_prepare_fails(
    session_factory, tmp_path, monkeypatch, completed, enabled, document_type, prepare_ok
) -> None:
    job_id = _job(session_factory, tmp_path, document_type=document_type)
    ocr_runner = FakeOcrRunner()
    monkeypatch.setattr(gemini_batch, "GeminiBatchRunner", lambda settings: FakeBatchRunner(prepare_ok=prepare_ok))
    monkeypatch.setattr(processing, "import_records_from_csv", lambda *a, **k: None)
    settings = Settings(storage_root=tmp_path, gemini_batch_enabled=enabled)

    processing.process_ocr_job(job_id, settings, session_factory, ocr_runner)

    assert len(ocr_runner.live_runs) == 1
    assert _get(session_factory, job_id).gemini_batch_stage is None


def test_tick_submits_prepared_jobs_then_finishes_them(session_factory, tmp_path, completed) -> None:
    job_ids = [
        _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.PREPARED) for _ in range(2)
    ]
    for job_id in job_ids:
        FakeBatchRunner().prepare(None, None, tmp_path / f"gemini-batch/{job_id}/prepared")
    runner, executor = FakeBatchRunner(), FakeExecutor()
    settings = _settings(tmp_path)

    first = gemini_batch.tick(settings, session_factory, executor, runner, FakeOcrRunner())

    # Submitted and, since the fake batch is already done, finished in the same pass.
    assert first == {"submitted": 2, "finished": 2}
    assert sorted(item["key_prefix"] for item in runner.submitted_items[0]) == sorted(str(j) for j in job_ids)
    assert sorted(completed) == sorted(job_ids)
    assert all(_get(session_factory, j).status == JobStatus.COMPLETED.value for j in job_ids)
    assert executor.submitted == []


def test_running_batch_is_left_alone(session_factory, tmp_path, completed) -> None:
    job_id = _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.SUBMITTED)
    with session_factory() as session:
        session.execute(update(OCRJob).where(OCRJob.id == job_id).values(gemini_batch_name="batches/one"))
        session.commit()
    executor = FakeExecutor()

    gemini_batch.tick(
        _settings(tmp_path),
        session_factory,
        executor,
        FakeBatchRunner(collect_state="JOB_STATE_RUNNING"),
        FakeOcrRunner(),
    )

    assert _get(session_factory, job_id).gemini_batch_stage == gemini_batch.SUBMITTED
    assert executor.submitted == [] and completed == []


def _submitted_job(session_factory, tmp_path, *, age=timedelta(0)):
    job_id = _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.SUBMITTED)
    FakeBatchRunner().prepare(None, None, tmp_path / f"gemini-batch/{job_id}/prepared")
    with session_factory() as session:
        session.execute(
            update(OCRJob)
            .where(OCRJob.id == job_id)
            .values(gemini_batch_name="batches/one", gemini_batch_updated_at=datetime.now(UTC) - age)
        )
        session.commit()
    return job_id


@pytest.mark.parametrize(
    ("runner", "age"),
    [
        (FakeBatchRunner(collect_state="JOB_STATE_FAILED"), timedelta(0)),
        (FakeBatchRunner(collect_state="JOB_STATE_EXPIRED"), timedelta(0)),
        (FakeBatchRunner(finish_ok=False), timedelta(0)),
        (FakeBatchRunner(collect_state="JOB_STATE_RUNNING"), timedelta(hours=31)),
    ],
    ids=["batch-failed", "batch-expired", "page-missing", "too-slow"],
)
def test_problems_fall_back_to_a_live_run(session_factory, tmp_path, completed, runner, age) -> None:
    job_id = _submitted_job(session_factory, tmp_path, age=age)
    executor = FakeExecutor()

    gemini_batch.tick(_settings(tmp_path), session_factory, executor, runner, FakeOcrRunner())

    job = _get(session_factory, job_id)
    assert (job.status, job.gemini_batch_stage) == (JobStatus.PENDING.value, gemini_batch.SYNC)
    assert executor.submitted == [job_id]
    assert completed == []


def test_submit_failure_falls_back_to_live(session_factory, tmp_path, completed) -> None:
    job_id = _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.PREPARED)
    FakeBatchRunner().prepare(None, None, tmp_path / f"gemini-batch/{job_id}/prepared")
    executor = FakeExecutor()

    gemini_batch.tick(
        _settings(tmp_path), session_factory, executor, FakeBatchRunner(submit_error="quota"), FakeOcrRunner()
    )

    assert _get(session_factory, job_id).gemini_batch_stage == gemini_batch.SYNC
    assert executor.submitted == [job_id]


def test_switching_off_runs_prepared_jobs_live_but_still_finishes_submitted_ones(
    session_factory, tmp_path, completed
) -> None:
    prepared = _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.PREPARED)
    submitted = _submitted_job(session_factory, tmp_path)
    executor = FakeExecutor()

    gemini_batch.tick(
        Settings(storage_root=tmp_path, gemini_batch_enabled=False),
        session_factory,
        executor,
        FakeBatchRunner(),
        FakeOcrRunner(),
    )

    assert executor.submitted == [prepared]
    assert completed == [submitted]


def test_live_sync_fallback_is_not_batched_again(session_factory, tmp_path, monkeypatch, completed) -> None:
    job_id = _job(session_factory, tmp_path, stage=gemini_batch.SYNC)
    ocr_runner = FakeOcrRunner()
    monkeypatch.setattr(gemini_batch, "GeminiBatchRunner", lambda settings: FakeBatchRunner())

    processing.process_ocr_job(job_id, _settings(tmp_path), session_factory, ocr_runner)

    assert len(ocr_runner.live_runs) == 1


def test_cancelled_batch_job_is_never_finished(session_factory, tmp_path, completed) -> None:
    job_id = _submitted_job(session_factory, tmp_path)
    with session_factory() as session:
        session.execute(update(OCRJob).where(OCRJob.id == job_id).values(status=JobStatus.CANCELLED.value))
        session.commit()
    runner = FakeBatchRunner()

    gemini_batch.tick(_settings(tmp_path), session_factory, FakeExecutor(), runner, FakeOcrRunner())

    assert runner.finished == [] and completed == []


def test_restart_and_stale_recovery_leave_batch_waiting_jobs_alone(session_factory, tmp_path) -> None:
    waiting = _submitted_job(session_factory, tmp_path)
    live = _job(session_factory, tmp_path, status=JobStatus.PROCESSING)
    long_ago = datetime.now(UTC) - timedelta(days=2)
    with session_factory() as session:
        session.execute(update(OCRJob).values(started_at=long_ago))
        stale = repositories.fail_stale_processing_jobs(session, datetime.now(UTC), 60)
        interrupted = repositories.fail_interrupted_jobs(session, datetime.now(UTC))
        session.commit()

    assert [job.id for job in stale] == [live]
    assert interrupted == []
    assert _get(session_factory, waiting).status == JobStatus.PROCESSING.value


def test_retry_clears_batch_state(session_factory, tmp_path) -> None:
    job_id = _job(session_factory, tmp_path, status=JobStatus.PROCESSING, stage=gemini_batch.SYNC)
    with session_factory() as session:
        repositories.mark_failed(session, job_id, "X", "x", datetime.now(UTC))
        repositories.mark_pending_for_retry(session, job_id)
        session.commit()

    assert _get(session_factory, job_id).gemini_batch_stage is None
