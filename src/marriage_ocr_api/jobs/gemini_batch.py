"""Gemini Batch Mode for handwritten OCR jobs -- half the Gemini price, with
results arriving in minutes to hours instead of seconds. Off unless
GEMINI_BATCH_ENABLED is set; meant for bulk backlogs, not everyday uploads.

A batched job stays PROCESSING throughout (so documents/batches, the UI and
"Stop processing" treat it like any running job) and moves through
`gemini_batch_stage`:

    process_ocr_job (worker)   PENDING -> PROCESSING, stage PREPARED
                               (marriage-ocr `gemini-batch-prepare`)
    tick: submit               PREPARED -> SUBMITTING -> SUBMITTED
                               (`gemini-batch-submit`, many jobs per batch)
    tick: collect              SUBMITTED -> FINISHING -> COMPLETED
                               (`gemini-batch-collect` + `gemini-batch-finish`,
                               then the same complete_job a live run uses)

Anything that goes wrong -- prepare/submit/finish failing, a page missing
from the results, a batch that fails/expires or runs past
GEMINI_BATCH_FALLBACK_HOURS, a worker dying mid-step -- falls back to the
normal live run (stage SYNC), so batching can delay a job but never lose it.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from marriage_ocr_api.batches.status import HANDWRITTEN_DOCUMENT_TYPES, TYPED_DOCUMENT_TYPES, DocumentType
from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.db.models import OCRJob
from marriage_ocr_api.jobs.runner import SubprocessOCRRunner
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.onedrive.runner import OneDriveFetchRunner
from marriage_ocr_api.storage.factory import get_storage_service

logger = logging.getLogger(__name__)

PREPARED = "PREPARED"
SUBMITTING = "SUBMITTING"
SUBMITTED = "SUBMITTED"
FINISHING = "FINISHING"
SYNC = "SYNC"
# A worker killed mid-step leaves these behind; after this long they're
# retried live instead of waiting forever.
_STUCK_STEP_AFTER = timedelta(hours=1)
_MISSING_RESULT_EXIT_CODE = 3


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class JobSubmitter(Protocol):
    def submit(self, job_id: UUID) -> object: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _rowcount(result: object) -> int:
    """Rows an UPDATE matched -- the conditional UPDATEs below double as
    claims, so two scheduler passes can't both act on the same job."""
    return int(getattr(result, "rowcount", 0) or 0)


class GeminiBatchRunner(OneDriveFetchRunner):
    """The marriage-ocr `gemini-batch-*` commands, run the same way (same
    checkout, cwd, PYTHONPATH) as the OneDrive fetch/classify commands."""

    def prepare(self, input_path: Path, config_path: Path, out_dir: Path) -> bool:
        result = self._run(
            [
                "gemini-batch-prepare",
                "--input",
                str(input_path),
                "--config",
                str(config_path),
                "--out-dir",
                str(out_dir),
            ]
        )
        if result.returncode != 0:
            logger.warning("gemini-batch-prepare failed (%s): %s", result.returncode, result.stderr[-500:])
        return result.returncode == 0

    def submit(self, items_path: Path, display_name: str) -> list[dict]:
        result = self._run(["gemini-batch-submit", "--items", str(items_path), "--display-name", display_name])
        if result.returncode != 0:
            raise RuntimeError(f"gemini-batch-submit exited {result.returncode}: {result.stderr[-500:]}")
        return list(json.loads(result.stdout))

    def collect(self, batch_name: str, out_dir: Path) -> dict:
        result = self._run(["gemini-batch-collect", "--batch", batch_name, "--out-dir", str(out_dir)])
        if result.returncode != 0:
            raise RuntimeError(f"gemini-batch-collect exited {result.returncode}: {result.stderr[-500:]}")
        return dict(json.loads(result.stdout))

    def finish(
        self,
        *,
        input_path: Path,
        config_path: Path,
        prepared_dir: Path,
        payload_dir: Path,
        key_prefix: str,
        output_path: Path,
        debug_path: Path,
    ) -> bool:
        result = self._run(
            [
                "gemini-batch-finish",
                "--input", str(input_path),
                "--config", str(config_path),
                "--prepared-dir", str(prepared_dir),
                "--payload-dir", str(payload_dir),
                "--key-prefix", key_prefix,
                "--output", str(output_path),
                "--debug", str(debug_path),
            ]
        )  # fmt: skip
        if result.returncode != 0:
            logger.warning("gemini-batch-finish failed (%s): %s", result.returncode, result.stderr[-500:])
        return result.returncode == 0


def _prepared_relative_dir(job_id: UUID) -> str:
    return f"gemini-batch/{job_id}/prepared"


def _prepared_files(prepared_dir: Path) -> list[Path]:
    manifest = json.loads((prepared_dir / "manifest.json").read_text(encoding="utf-8"))
    return [prepared_dir / "manifest.json", *(prepared_dir / page["image"] for page in manifest["pages"])]


def delete_prepared_from_object_storage(settings: Settings, job_id: UUID) -> int:
    """Remove a finished job's Batch Mode helper copies from object storage --
    ONLY gemini-batch/<job>/prepared/manifest.json and the page images that
    manifest names, each deleted by its exact key (nothing is deleted by
    prefix). These are resized copies made just to send pages to Gemini;
    the job's real input, pages and outputs are never touched. Returns the
    number of objects deleted; a no-op unless STORAGE_BACKEND=s3."""
    if settings.storage_backend != "s3":
        return 0
    relative_dir = _prepared_relative_dir(job_id)
    manifest_key = f"{relative_dir}/manifest.json"
    manifest_path = settings.storage_root.resolve() / manifest_key
    storage = get_storage_service(settings)
    if not manifest_path.is_file():
        if not storage.exists(manifest_key):
            return 0
        storage.materialize(manifest_key, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    keys = [f"{relative_dir}/{page['image']}" for page in manifest.get("pages", [])] + [manifest_key]
    for key in keys:
        storage.delete(key)
    return len(keys)


def should_batch(settings: Settings, job: OCRJob) -> bool:
    """Handwritten pages always use Gemini, so they batch whenever Batch Mode
    is on; typed certificates only once TYPED_READER=gemini."""
    if not settings.gemini_batch_enabled or job.gemini_batch_stage is not None:
        return False
    document_type = DocumentType(job.document_type)
    if document_type in HANDWRITTEN_DOCUMENT_TYPES:
        return True
    return document_type in TYPED_DOCUMENT_TYPES and settings.typed_reader == "gemini"


def _set_stage(session: Session, job_ids: list[UUID], stage: str, *, name: str | None = None) -> None:
    values: dict[str, object] = {"gemini_batch_stage": stage, "gemini_batch_updated_at": _now()}
    if name is not None:
        values["gemini_batch_name"] = name
    session.execute(update(OCRJob).where(OCRJob.id.in_(job_ids)).values(**values))


def prepare_job(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    job: OCRJob,
    input_path: Path,
    config_path: Path,
    runner: GeminiBatchRunner | None = None,
) -> bool:
    """Save the job's batch request (stage PREPARED). False -- and nothing
    changed -- if it can't be batched; the caller then runs it live."""
    runner = runner or GeminiBatchRunner(settings)
    relative_dir = _prepared_relative_dir(job.id)
    prepared_dir = settings.storage_root.resolve() / relative_dir
    shutil.rmtree(prepared_dir, ignore_errors=True)
    try:
        if not runner.prepare(input_path, config_path, prepared_dir):
            return False
        if settings.storage_backend == "s3":
            storage = get_storage_service(settings)
            for path in _prepared_files(prepared_dir):
                storage.put_file(path, f"{relative_dir}/{path.name}")
        with session_factory() as session:
            _set_stage(session, [job.id], PREPARED)
            session.commit()
    except Exception:
        logger.exception("could not prepare job %s for Gemini batch; running it live", job.id)
        return False
    logger.info("prepared job %s for Gemini batch", job.id)
    return True


def _materialize(settings: Settings, relative_path: str, destination: Path) -> None:
    if not destination.is_file() and settings.storage_backend == "s3":
        get_storage_service(settings).materialize(relative_path, destination)


def _materialize_prepared(settings: Settings, job_id: UUID) -> Path:
    relative_dir = _prepared_relative_dir(job_id)
    prepared_dir = settings.storage_root.resolve() / relative_dir
    _materialize(settings, f"{relative_dir}/manifest.json", prepared_dir / "manifest.json")
    for path in _prepared_files(prepared_dir)[1:]:
        _materialize(settings, f"{relative_dir}/{path.name}", path)
    return prepared_dir


def fall_back_to_live(
    session_factory: sessionmaker[Session] | SessionFactory,
    executor: JobSubmitter,
    job_ids: list[UUID],
    reason: str,
) -> None:
    """Put still-PROCESSING batched jobs back through the normal live run."""
    requeued: list[UUID] = []
    with session_factory() as session:
        for job_id in job_ids:
            changed = _rowcount(
                session.execute(
                    update(OCRJob)
                    .where(OCRJob.id == job_id, OCRJob.status == JobStatus.PROCESSING.value)
                    .values(
                        status=JobStatus.PENDING.value,
                        started_at=None,
                        gemini_batch_stage=SYNC,
                        gemini_batch_updated_at=_now(),
                    )
                )
            )
            if changed:
                requeued.append(job_id)
        session.commit()
    for job_id in requeued:
        executor.submit(job_id)
    if requeued:
        logger.warning("running %d job(s) live instead of via Gemini batch: %s", len(requeued), reason)


def _claim(session: Session, job_id: UUID, from_stage: str, to_stage: str) -> bool:
    return bool(
        _rowcount(
            session.execute(
                update(OCRJob)
                .where(
                    OCRJob.id == job_id,
                    OCRJob.status == JobStatus.PROCESSING.value,
                    OCRJob.gemini_batch_stage == from_stage,
                )
                .values(gemini_batch_stage=to_stage, gemini_batch_updated_at=_now())
            )
        )
    )


def _submit_prepared(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    executor: JobSubmitter,
    runner: GeminiBatchRunner,
) -> int:
    with session_factory() as session:
        candidates = list(
            session.scalars(
                select(OCRJob.id)
                .where(OCRJob.status == JobStatus.PROCESSING.value, OCRJob.gemini_batch_stage == PREPARED)
                .order_by(OCRJob.gemini_batch_updated_at)
                .limit(settings.gemini_batch_max_jobs)
            )
        )
        job_ids = [job_id for job_id in candidates if _claim(session, job_id, PREPARED, SUBMITTING)]
        session.commit()
    if not job_ids:
        return 0

    work_dir = settings.storage_root.resolve() / "gemini-batch" / "submit" / _now().strftime("%Y%m%dT%H%M%S%f")
    try:
        items = [
            {"key_prefix": str(job_id), "prepared_dir": str(_materialize_prepared(settings, job_id))}
            for job_id in job_ids
        ]
        work_dir.mkdir(parents=True, exist_ok=True)
        items_path = work_dir / "items.json"
        items_path.write_text(json.dumps(items), encoding="utf-8")
        batches = runner.submit(items_path, display_name=f"marriage-be-{work_dir.name}")
        with session_factory() as session:
            for batch in batches:
                _set_stage(session, [UUID(key) for key in batch["keys"]], SUBMITTED, name=batch["name"])
            session.commit()
        logger.info("submitted %d job(s) in %d Gemini batch(es)", len(job_ids), len(batches))
        return len(job_ids)
    except Exception as error:
        logger.exception("Gemini batch submit failed")
        fall_back_to_live(session_factory, executor, job_ids, f"submit failed: {error}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _finish_job(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    executor: JobSubmitter,
    runner: GeminiBatchRunner,
    ocr_runner: SubprocessOCRRunner,
    job_id: UUID,
    payload_dir: Path,
) -> bool:
    from marriage_ocr_api.jobs.processing import _ensure_input_materialized, complete_job

    with session_factory() as session:
        if not _claim(session, job_id, SUBMITTED, FINISHING):
            session.commit()
            return False
        session.commit()
        job = session.get(OCRJob, job_id)
        assert job is not None
        session.expunge(job)
    storage_root = settings.storage_root.resolve()
    input_path = storage_root / job.input_relative_path
    debug_path = storage_root / job.debug_relative_path
    # Same output file a live run of this job would write (process_ocr_job).
    is_typed = DocumentType(job.document_type) in TYPED_DOCUMENT_TYPES
    output_path = debug_path.parent / "output" / ("result.csv" if is_typed else "result.xlsx")
    try:
        _ensure_input_materialized(settings, input_path, job.input_relative_path)
        prepared_dir = _materialize_prepared(settings, job_id)
        ok = runner.finish(
            input_path=input_path,
            config_path=ocr_runner.config_path_for(DocumentType(job.document_type)),
            prepared_dir=prepared_dir,
            payload_dir=payload_dir,
            key_prefix=str(job_id),
            output_path=output_path,
            debug_path=debug_path,
        )
        if not ok or not output_path.is_file():
            fall_back_to_live(session_factory, executor, [job_id], "finish failed or a page had no batch result")
            return False
        complete_job(settings, session_factory, job, output_path, is_typed=is_typed)
        return True
    except Exception as error:
        logger.exception("finishing Gemini batch job %s failed", job_id)
        fall_back_to_live(session_factory, executor, [job_id], f"finish error: {error}")
        return False


def _collect_submitted(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    executor: JobSubmitter,
    runner: GeminiBatchRunner,
    ocr_runner: SubprocessOCRRunner,
) -> int:
    with session_factory() as session:
        names = [
            name
            for name in session.scalars(
                select(OCRJob.gemini_batch_name)
                .where(OCRJob.status == JobStatus.PROCESSING.value, OCRJob.gemini_batch_stage == SUBMITTED)
                .distinct()
            )
            if name
        ]
    finished = 0
    for name in names:
        with session_factory() as session:
            rows = list(
                session.execute(
                    select(OCRJob.id, OCRJob.gemini_batch_updated_at).where(
                        OCRJob.status == JobStatus.PROCESSING.value,
                        OCRJob.gemini_batch_stage == SUBMITTED,
                        OCRJob.gemini_batch_name == name,
                    )
                )
            )
        job_ids = [row.id for row in rows]
        payload_dir = (
            settings.storage_root.resolve() / "gemini-batch" / "results" / re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        )
        try:
            summary = runner.collect(name, payload_dir)
        except Exception:
            logger.exception("could not check Gemini batch %s; will retry next tick", name)
            continue
        if not summary.get("done"):
            oldest = min((row.gemini_batch_updated_at for row in rows if row.gemini_batch_updated_at), default=None)
            if oldest is not None and _now() - _aware(oldest) > timedelta(hours=settings.gemini_batch_fallback_hours):
                fall_back_to_live(session_factory, executor, job_ids, f"batch {name} still {summary.get('state')}")
            continue
        if summary.get("state") != "JOB_STATE_SUCCEEDED":
            fall_back_to_live(session_factory, executor, job_ids, f"batch {name} ended {summary.get('state')}")
            continue
        for job_id in job_ids:
            finished += _finish_job(settings, session_factory, executor, runner, ocr_runner, job_id, payload_dir)
        shutil.rmtree(payload_dir, ignore_errors=True)
    return finished


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _recover_stuck_steps(session_factory: sessionmaker[Session] | SessionFactory, executor: JobSubmitter) -> None:
    """A job left in SUBMITTING/FINISHING (worker died mid-step), or PREPARED
    after batching was switched off, is run live instead."""
    cutoff = _now() - _STUCK_STEP_AFTER
    with session_factory() as session:
        stuck = [
            row.id
            for row in session.execute(
                select(OCRJob.id, OCRJob.gemini_batch_updated_at).where(
                    OCRJob.status == JobStatus.PROCESSING.value,
                    OCRJob.gemini_batch_stage.in_([SUBMITTING, FINISHING]),
                )
            )
            if row.gemini_batch_updated_at is None or _aware(row.gemini_batch_updated_at) < cutoff
        ]
    if stuck:
        fall_back_to_live(session_factory, executor, stuck, "interrupted mid-step")


def tick(
    settings: Settings,
    session_factory: sessionmaker[Session] | SessionFactory,
    executor: JobSubmitter,
    runner: GeminiBatchRunner | None = None,
    ocr_runner: SubprocessOCRRunner | None = None,
) -> dict[str, int]:
    """One scheduler pass: submit prepared jobs, finish completed batches.
    Collecting keeps running with batching switched off, so jobs already
    submitted still finish; jobs merely PREPARED then run live."""
    runner = runner or GeminiBatchRunner(settings)
    ocr_runner = ocr_runner or SubprocessOCRRunner(settings)
    _recover_stuck_steps(session_factory, executor)
    submitted = 0
    if settings.gemini_batch_enabled:
        submitted = _submit_prepared(settings, session_factory, executor, runner)
    else:
        with session_factory() as session:
            leftover = list(
                session.scalars(
                    select(OCRJob.id).where(
                        OCRJob.status == JobStatus.PROCESSING.value, OCRJob.gemini_batch_stage == PREPARED
                    )
                )
            )
        if leftover:
            fall_back_to_live(session_factory, executor, leftover, "Gemini batch mode switched off")
    finished = _collect_submitted(settings, session_factory, executor, runner, ocr_runner)
    return {"submitted": submitted, "finished": finished}
