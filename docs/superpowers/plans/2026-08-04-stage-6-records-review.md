# Stage 6 Records and Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OCR record materialization, review history, and record review endpoints to the FastAPI backend.

**Architecture:** Keep record persistence, review rules, and HTTP routing separate from the existing job pipeline. Job completion will hand machine-readable OCR output to a dedicated record-import service, while review endpoints read and mutate only the record tables. Corrections become immutable revisions so the current record state and its history stay easy to reason about and export later.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2.x, Psycopg 3, Alembic, PostgreSQL 16, Pytest, Ruff, Mypy, Docker Compose.

## Global Constraints

- The repositories must remain independent; do not convert them into a monorepo, do not copy the OCR source tree into the backend, and do not generate a ZIP archive.
- Changes to `marriage-ocr` are allowed only when a small, backward-compatible CLI integration contract is genuinely missing.
- Do not move OCR algorithms into `marriage-be`.
- Tests must not call Google Vision or Gemini.
- The default backend test suite must not require the real upstream OCR package.
- Preserve the existing CLI subprocess boundary and the current job pipeline.
- Store timestamps in UTC.
- Prefer a `pyproject.toml`-based installation.

---

### Task 1: Record tables, status model, and migration

**Files:**
- Create: `src/marriage_ocr_api/records/__init__.py`
- Create: `src/marriage_ocr_api/records/status.py`
- Create: `src/marriage_ocr_api/records/models.py`
- Create: `src/marriage_ocr_api/records/schemas.py`
- Modify: `src/marriage_ocr_api/db/base.py`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0002_create_ocr_records.py`
- Create: `tests/unit/test_record_models.py`
- Create: `tests/integration/test_record_migration.py`

**Interfaces:**
- Consumes: `Base`, SQLAlchemy `Session`, UUIDs, UTC timestamps.
- Produces: `RecordStatus`, `OCRRecord`, `RecordRevision`, `ReviewDecision`, and the Alembic migration that creates the review tables and indexes.

- [ ] **Step 1: Write the failing tests**

```python
def test_record_models_register_with_metadata() -> None:
    assert "ocr_records" in Base.metadata.tables
    assert "record_revisions" in Base.metadata.tables


def test_record_migration_creates_and_drops_review_tables(runner: AlembicRunner) -> None:
    runner.upgrade("head")
    assert runner.has_table("ocr_records")
    assert runner.has_table("record_revisions")

    runner.downgrade("base")
    assert not runner.has_table("ocr_records")
    assert not runner.has_table("record_revisions")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_record_models.py tests/integration/test_record_migration.py`
Expected: import or assertion failures because the record package and migration do not exist yet.

- [ ] **Step 3: Implement the minimal model layer**

```python
class RecordStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OCRRecord(Base):
    __tablename__ = "ocr_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("ocr_jobs.id"), index=True, nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    field_values: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    validation_issues: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_record_models.py tests/integration/test_record_migration.py`
Expected: both tests pass against SQLite for the unit test and PostgreSQL/Alembic for the migration test.

---

### Task 2: Record repository and import/review service

**Files:**
- Create: `src/marriage_ocr_api/records/repositories.py`
- Create: `src/marriage_ocr_api/records/service.py`
- Create: `src/marriage_ocr_api/records/importer.py`
- Create: `tests/fixtures/ocr_records.json`
- Create: `tests/unit/test_record_repository.py`
- Create: `tests/unit/test_record_service.py`

**Interfaces:**
- Consumes: `OCRRecord`, `RecordRevision`, `RecordStatus`, `Session`, and the JSON artifact path emitted by the OCR CLI contract.
- Produces: `create_record`, `get_record`, `list_records`, `count_records`, `append_revision`, `apply_correction`, `approve_record`, `reject_record`, `bulk_approve_records`, and `import_records_from_json`.

- [ ] **Step 1: Write the failing tests**

```python
def test_import_records_from_json_is_idempotent(session: Session, tmp_path: Path) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614175100")
    payload = tmp_path / "ocr_records.json"
    payload.write_text(
        """
        {"records":[
          {"source_key":"page-1-row-1","field_values":{"full_name":"Ada Lovelace"},"confidence":0.97,"validation_issues":[]},
          {"source_key":"page-1-row-2","field_values":{"full_name":"Grace Hopper"},"confidence":0.95,"validation_issues":[]}
        ]}
        """.strip(),
        encoding="utf-8",
    )

    created_first = import_records_from_json(session, job_id, payload)
    created_second = import_records_from_json(session, job_id, payload)

    assert created_first == 2
    assert created_second == 0
    assert count_records(session, job_id=job_id) == 2


def test_apply_correction_creates_revision_and_updates_current_state(session: Session) -> None:
    job_id = UUID("123e4567-e89b-12d3-a456-426614175101")
    record = create_record(
        session,
        job_id=job_id,
        source_key="page-1-row-1",
        field_values={"full_name": "Ada Lovelace"},
        confidence=0.97,
        validation_issues=[],
    )

    updated = apply_correction(
        session,
        record.id,
        expected_version=1,
        field_values={"full_name": "Ada Byron"},
        reviewer="reviewer@example.com",
        note="corrected surname",
    )

    revisions = list_revisions(session, record.id)

    assert updated.version == 2
    assert updated.field_values["full_name"] == "Ada Byron"
    assert len(revisions) == 1
    assert revisions[0].note == "corrected surname"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_record_repository.py tests/unit/test_record_service.py`
Expected: missing symbols and failing assertions because repository and service logic are absent.

- [ ] **Step 3: Implement the repository and service**

```python
def import_records_from_json(session: Session, job_id: UUID, payload_path: Path) -> int:
    data = json.loads(payload_path.read_text(encoding="utf-8"))
    created = 0
    for item in data["records"]:
        if create_record_if_missing(session, job_id=job_id, payload=item):
            created += 1
    return created


def apply_correction(
    session: Session,
    record_id: UUID,
    *,
    expected_version: int,
    field_values: dict[str, object],
    reviewer: str | None,
    note: str | None,
) -> OCRRecord:
    record = get_record_or_raise(session, record_id)
    if record.version != expected_version:
        raise RecordConflictError("The record has been modified by another review action.")
    append_revision(
        session,
        record_id=record.id,
        previous_state=record.field_values,
        new_state=field_values,
        reviewer=reviewer,
        note=note,
    )
    record.field_values = field_values
    record.reviewed_by = reviewer
    record.reviewed_at = utcnow()
    record.version += 1
    return record
```

Keep the record state transition rules strict:

- import creates `PENDING_REVIEW` rows only once per `job_id` + `source_key`;
- corrections create a `RecordRevision` row and increment the record version;
- approve/reject are idempotent only when the requested final state already matches the current state;
- stale `expected_version` values raise a conflict error.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_record_repository.py tests/unit/test_record_service.py`
Expected: both suites pass.

---

### Task 3: Record review API and response schemas

**Files:**
- Create: `src/marriage_ocr_api/records/api.py`
- Create: `src/marriage_ocr_api/records/routers.py`
- Create: `src/marriage_ocr_api/records/response_models.py`
- Modify: `src/marriage_ocr_api/api/routers/__init__.py`
- Modify: `src/marriage_ocr_api/main.py`
- Create: `tests/unit/test_record_routes.py`
- Create: `tests/integration/test_record_review_api.py`

**Interfaces:**
- Consumes: record service functions, `ApiError`, and existing request-id/error handling.
- Produces: `GET /api/v1/records`, `GET /api/v1/records/{record_id}`, `GET /api/v1/jobs/{job_id}/records`, `GET /api/v1/records/{record_id}/revisions`, `PATCH /api/v1/records/{record_id}`, `POST /api/v1/records/{record_id}/approve`, `POST /api/v1/records/{record_id}/reject`, and `POST /api/v1/records/bulk-approve`.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_get_patch_approve_reject_and_revisions(client: TestClient) -> None:
    record_id = UUID("123e4567-e89b-12d3-a456-426614175200")
    list_response = client.get("/api/v1/records", params={"limit": 20, "offset": 0})
    assert list_response.status_code == 200

    detail_response = client.get(f"/api/v1/records/{record_id}")
    assert detail_response.status_code == 200

    revisions_response = client.get(f"/api/v1/records/{record_id}/revisions")
    assert revisions_response.status_code == 200

    patch_response = client.patch(
        f"/api/v1/records/{record_id}",
        json={"version": 1, "field_values": {"full_name": "Updated Name"}, "note": "fixed spelling"},
    )
    assert patch_response.status_code == 200

    approve_response = client.post(f"/api/v1/records/{record_id}/approve", json={"version": 2})
    assert approve_response.status_code == 200

    reject_response = client.post(f"/api/v1/records/{record_id}/reject", json={"version": 2, "reason": "duplicate"})
    assert reject_response.status_code == 200

    bulk_response = client.post("/api/v1/records/bulk-approve", json={"record_ids": [str(record_id)]})
    assert bulk_response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_record_routes.py tests/integration/test_record_review_api.py`
Expected: route lookup or response assertion failures because the record router is not wired yet.

- [ ] **Step 3: Implement the API layer**

```python
class RecordResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: RecordStatus
    field_values: dict[str, object]
    confidence: float | None
    validation_issues: list[str]
    reviewed_by: str | None
    reviewed_at: datetime | None
    version: int
```

Add thin route handlers only:

- parse pagination and review payloads;
- call the record service;
- translate repository conflicts into `ApiError(409, "RECORD_CONFLICT", "The record has been modified by another review action.")`;
- translate missing rows into `ApiError(404, "RECORD_NOT_FOUND", "OCR record not found.")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_record_routes.py tests/integration/test_record_review_api.py`
Expected: the record API and review API tests pass.

---

### Task 4: Job completion import hook and lifecycle integration

**Files:**
- Modify: `src/marriage_ocr_api/jobs/executor.py`
- Modify: `tests/fixtures/fake_ocr_cli.py`
- Modify: `tests/integration/test_job_lifecycle.py`
- Create: `tests/integration/test_job_record_import.py`

**Interfaces:**
- Consumes: the successful job result path, the OCR JSON sidecar path, and `import_records_from_json`.
- Produces: record import immediately after job completion, plus a lifecycle test proving records appear after a completed job.

- [ ] **Step 1: Write the failing tests**

```python
def test_completed_job_imports_records(tmp_path: Path, client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        files={"file": ("register.pdf", BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"), "application/pdf")},
    )
    job_id = response.json()["id"]

    # wait for fake OCR completion
    job_response = client.get(f"/api/v1/jobs/{job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "COMPLETED"

    records_response = client.get(f"/api/v1/jobs/{job_id}/records")
    assert records_response.status_code == 200
    assert records_response.json()["total"] == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/integration/test_job_lifecycle.py tests/integration/test_job_record_import.py`
Expected: the job finishes but no records exist yet, so the new assertions fail.

- [ ] **Step 3: Wire the import into job completion**

```python
if failure_code is None:
    completed_at = datetime.now(UTC)
    with self._session() as session:
        repositories.mark_completed(session, job_id, paths.output_relative_path, completed_at)
        import_records_from_json(
            session,
            job_id,
            paths.output_result_path.with_suffix(".json"),
        )
        session.commit()
```

Update the fake OCR fixture so successful runs emit both the XLSX artifact and the machine-readable JSON sidecar that the import service reads.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/integration/test_job_lifecycle.py tests/integration/test_job_record_import.py`
Expected: the job completion path imports records and both integration tests pass.

---

### Task 5: Full Stage 6 verification

**Files:**
- Modify: any files changed in Tasks 1-4 if test feedback requires cleanup

**Interfaces:**
- Consumes: the full record/review surface and the job import hook.
- Produces: a stable backend that supports record listing, detail, corrections, revisions, approval, rejection, bulk approval, and completed-job import.

- [ ] **Step 1: Run the focused Stage 6 suites**

Run:
```bash
pytest -q tests/unit/test_record_models.py tests/unit/test_record_repository.py tests/unit/test_record_service.py tests/unit/test_record_routes.py
pytest -q tests/integration/test_record_migration.py tests/integration/test_record_review_api.py tests/integration/test_job_lifecycle.py tests/integration/test_job_record_import.py
```

- [ ] **Step 2: Run the backend regression suite**

Run:
```bash
pytest -q
ruff check .
mypy src
```

- [ ] **Step 3: Stop for review**

Report the exact test commands and outputs before making any further changes outside Stage 6.
