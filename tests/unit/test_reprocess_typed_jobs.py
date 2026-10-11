from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marriage_ocr_api.batches.status import DocumentType
from marriage_ocr_api.db.base import Base
from marriage_ocr_api.db.repositories import create_job
from marriage_ocr_api.jobs.status import JobStatus
from marriage_ocr_api.records.repositories import create_record

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reprocess_typed_jobs.py"
_spec = importlib.util.spec_from_file_location("reprocess_typed_jobs", _SCRIPT)
reprocess = importlib.util.module_from_spec(_spec)
sys.modules["reprocess_typed_jobs"] = reprocess
_spec.loader.exec_module(reprocess)


def _session():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _job_with_record(session, *, missing, version=1, reviewer=None, status="PENDING_REVIEW"):
    job_id = uuid4()
    create_job(
        session,
        id=job_id,
        status=JobStatus.COMPLETED,
        document_type=DocumentType.TYPED_NIKAH_LEGACY,
        original_filename="a.pdf",
        stored_filename="source.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
        input_relative_path=f"jobs/{job_id}/input/source.pdf",
        debug_relative_path=f"jobs/{job_id}/debug",
        stdout_log_relative_path=f"jobs/{job_id}/logs/o.log",
        stderr_log_relative_path=f"jobs/{job_id}/logs/e.log",
        ocr_git_ref="abc",
    )
    create_record(
        session,
        job_id=job_id,
        source_key="source.pdf",
        field_values={"Bil": "1/92"},
        confidence=None,
        validation_issues=[],
        missing_fields=missing,
        version=version,
        reviewed_by=reviewer,
    )
    session.commit()
    return job_id


def test_selects_only_untouched_jobs_missing_mas_kahwin() -> None:
    session = _session()
    eligible = _job_with_record(session, missing=["Mas Kahwin"])
    _job_with_record(session, missing=["Mas Kahwin"], version=2, reviewer="r@example.com")  # human edited
    _job_with_record(session, missing=["IC Suami"])  # different problem
    _job_with_record(session, missing=[])  # not flagged

    args = Namespace(batch_id=None, created_on=None, limit=0)
    assert reprocess._eligible_job_ids(session, args) == [eligible]
