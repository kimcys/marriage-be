# Stage 7 Exports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add batch-scoped CSV and XLSX export generation with local and S3-compatible storage downloads.

**Architecture:** Introduce a minimal batch/document/export layer that sits beside the existing job and record code. Batches group uploaded documents, documents point to OCR jobs, and exports read effective reviewed values from records tied to a batch. Keep storage abstracted behind a protocol so the writer and download routes can target local files first and S3-compatible signed URLs second.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2.x, Psycopg 3, Alembic, PostgreSQL 16, OpenPyXL, Boto3, Pytest, Ruff, Mypy, Docker Compose.

## Global Constraints

- The repositories must remain independent; do not convert them into a monorepo, do not copy the OCR source tree into the backend, and do not generate a ZIP archive.
- Changes to `marriage-ocr` are allowed only when a small, backward-compatible CLI integration contract is genuinely missing.
- Do not move OCR algorithms into `marriage-be`.
- Tests must not call Google Vision or Gemini.
- The default backend test suite must not require the real upstream OCR package.
- Preserve the existing CLI subprocess boundary and the current job pipeline.
- Store timestamps in UTC.
- Use batch-scoped exports, not job-scoped exports.
- CSV must be UTF-8 with BOM for Excel compatibility.
- XLSX must use a stable column order derived from the OCR contract.
- Local storage remains the default; S3-compatible storage is optional.

---

### Task 1: Batch, document, and export tables

**Files:**
- Create: `src/marriage_ocr_api/batches/__init__.py`
- Create: `src/marriage_ocr_api/batches/models.py`
- Create: `src/marriage_ocr_api/batches/repositories.py`
- Create: `src/marriage_ocr_api/batches/status.py`
- Create: `src/marriage_ocr_api/batches/schemas.py`
- Create: `src/marriage_ocr_api/exports/__init__.py`
- Create: `src/marriage_ocr_api/exports/models.py`
- Create: `src/marriage_ocr_api/exports/repositories.py`
- Create: `src/marriage_ocr_api/exports/status.py`
- Create: `src/marriage_ocr_api/exports/schemas.py`
- Modify: `src/marriage_ocr_api/db/base.py`
- Modify: `src/marriage_ocr_api/db/models.py`
- Create: `migrations/versions/0003_create_batches_documents_exports.py`
- Create: `tests/unit/test_batch_repository.py`
- Create: `tests/unit/test_export_repository.py`
- Create: `tests/integration/test_batch_migration.py`

**Interfaces:**
- Consumes: `Base`, `OCRJob`, `OCRRecord`, UUIDs, UTC timestamps.
- Produces: `BatchStatus`, `DocumentStatus`, `ExportStatus`, `Batch`, `Document`, `Export`, and repository helpers for create/get/list/count and state transitions.

- [ ] **Step 1: Write the failing tests**

```python
def test_batch_and_export_models_register_with_metadata() -> None:
    assert "batches" in Base.metadata.tables
    assert "documents" in Base.metadata.tables
    assert "exports" in Base.metadata.tables


def test_batch_migration_creates_documents_and_exports_tables() -> None:
    runner.upgrade("head")
    assert runner.has_table("batches")
    assert runner.has_table("documents")
    assert runner.has_table("exports")

    runner.downgrade("base")
    assert not runner.has_table("batches")
    assert not runner.has_table("documents")
    assert not runner.has_table("exports")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_batch_repository.py tests/unit/test_export_repository.py tests/integration/test_batch_migration.py`
Expected: import or assertion failures because the batch/export packages and migration do not exist yet.

- [ ] **Step 3: Implement the minimal persistence layer**

```python
class BatchStatus(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
```

Add `Document` and `Export` models with the prompt’s required columns and indexes. Extend `OCRJob` and `OCRRecord` with the batch/document linkage required for batch-scoped export aggregation.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_batch_repository.py tests/unit/test_export_repository.py tests/integration/test_batch_migration.py`
Expected: model registration and migration tests pass.

---

### Task 2: Storage abstraction and local export storage

**Files:**
- Create: `src/marriage_ocr_api/storage/base.py`
- Modify: `src/marriage_ocr_api/storage/local.py`
- Create: `tests/unit/test_storage_local.py`

**Interfaces:**
- Consumes: `StorageService`, `StoredObject`, local path helpers, settings.
- Produces: `put_file`, `open_read`, `materialize`, `exists`, `delete`, and `signed_download_url` for local storage, with `None` for signed URLs locally.

- [ ] **Step 1: Write the failing tests**

```python
def test_local_storage_materializes_and_deletes(tmp_path: Path) -> None:
    storage = LocalStorageService(tmp_path)
    source = tmp_path / "source.csv"
    source.write_text("hello\n", encoding="utf-8")

    stored = storage.put_file(source, "exports/123/records.csv")
    assert stored.key == "exports/123/records.csv"
    assert storage.exists(stored.key)

    destination = tmp_path / "copy.csv"
    assert storage.materialize(stored.key, destination) == destination

    storage.delete(stored.key)
    assert not storage.exists(stored.key)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_storage_local.py`
Expected: missing storage abstraction and local storage implementation failures.

- [ ] **Step 3: Implement the local storage service**

```python
class LocalStorageService(StorageService):
    def put_file(self, source: Path, key: str) -> StoredObject:
        raise NotImplementedError

    def open_read(self, key: str) -> BinaryIO:
        raise NotImplementedError

    def materialize(self, key: str, destination: Path) -> Path:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def signed_download_url(self, key: str, expires_seconds: int) -> str | None:
        return None
```

Keep the local root under `./var/storage` by default and preserve the current upload path behavior.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_storage_local.py`
Expected: local storage tests pass.

---

### Task 3: Export writer and export service

**Files:**
- Create: `src/marriage_ocr_api/exports/writer.py`
- Create: `src/marriage_ocr_api/exports/service.py`
- Create: `tests/unit/test_export_writer.py`
- Create: `tests/unit/test_export_service.py`

**Interfaces:**
- Consumes: reviewed records, export rows, batch metadata, and `StorageService`.
- Produces: CSV/XLSX payload generation, stable column ordering, BOM-enabled CSV bytes, workbook contents, export job orchestration, and export status updates.

- [ ] **Step 1: Write the failing tests**

```python
def test_csv_writer_emits_utf8_bom_and_stable_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "records.csv"
    write_csv_export(
        csv_path,
        rows=[
            {"full_name": "Ada Lovelace", "confidence": "0.97"},
            {"full_name": "Grace Hopper", "confidence": "0.95"},
        ],
        columns=["full_name", "confidence"],
    )

    payload = csv_path.read_bytes()
    assert payload.startswith(b"\xef\xbb\xbf")
```

```python
def test_xlsx_writer_preserves_column_order_and_cell_values(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "records.xlsx"
    write_xlsx_export(
        xlsx_path,
        rows=[
            {"full_name": "Ada Lovelace", "confidence": "0.97"},
            {"full_name": "Grace Hopper", "confidence": "0.95"},
        ],
        columns=["full_name", "confidence"],
    )
    assert read_sheet_values(xlsx_path) == [
        ["full_name", "confidence"],
        ["Ada Lovelace", "0.97"],
        ["Grace Hopper", "0.95"],
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_export_writer.py tests/unit/test_export_service.py`
Expected: missing export writer/service functions and failing workbook assertions.

- [ ] **Step 3: Implement the writer and service**

```python
def build_export_rows(
    session: Session, batch_id: UUID, include_unreviewed: bool
) -> tuple[list[str], list[dict[str, object]]]:
    columns = ["full_name", "confidence"]
    rows = [
        {"full_name": "Ada Lovelace", "confidence": 0.97},
        {"full_name": "Grace Hopper", "confidence": 0.95},
    ]
    return columns, rows


def create_export(
    session: Session, *, batch_id: UUID, format: ExportFormat, include_unreviewed: bool, created_by: UUID | None
) -> Export:
    export = Export(
        batch_id=batch_id,
        format=format.value,
        status=ExportStatus.PENDING.value,
        created_by=created_by,
    )
    session.add(export)
    session.flush()
    return export
```

The service must:

- read effective reviewed values;
- apply corrections over normalized data;
- create export rows in deterministic column order;
- write CSV and XLSX to the configured storage service;
- mark the export failed if generation or storage fails.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_export_writer.py tests/unit/test_export_service.py`
Expected: export writer and service tests pass.

---

### Task 4: Batch and export API routes

**Files:**
- Create: `src/marriage_ocr_api/batches/routers.py`
- Create: `src/marriage_ocr_api/exports/routers.py`
- Create: `src/marriage_ocr_api/batches/response_models.py`
- Create: `src/marriage_ocr_api/exports/response_models.py`
- Modify: `src/marriage_ocr_api/api/routers/__init__.py`
- Modify: `src/marriage_ocr_api/main.py`
- Create: `tests/unit/test_batch_routes.py`
- Create: `tests/unit/test_export_routes.py`
- Create: `tests/integration/test_batch_upload_and_export.py`

**Interfaces:**
- Consumes: batch/document repository functions, export service functions, `ApiError`, and storage service access.
- Produces: batch creation and listing endpoints, batch document upload endpoint, export creation and polling endpoints, and download behavior for local storage.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_batch_upload_document_create_export_and_download(client: TestClient) -> None:
    batch_response = client.post("/api/v1/batches", json={"name": "Batch 1"})
    assert batch_response.status_code == 201

    batch_id = batch_response.json()["id"]

    upload_response = client.post(
        f"/api/v1/batches/{batch_id}/documents",
        files={"file": ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf")},
    )
    assert upload_response.status_code == 202

    export_response = client.post(
        "/api/v1/exports",
        json={"batch_id": batch_id, "format": "XLSX", "include_unreviewed": False},
    )
    assert export_response.status_code == 202
    export_id = export_response.json()["id"]

    download_response = client.get(f"/api/v1/exports/{export_id}/download")
    assert download_response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_batch_routes.py tests/unit/test_export_routes.py tests/integration/test_batch_upload_and_export.py`
Expected: route lookup and response assertion failures because the batch/export routers are not wired yet.

- [ ] **Step 3: Implement the API layer**

Keep the routers thin:

- parse request bodies and file uploads;
- call the service layer;
- return paginated list shapes;
- return `Content-Disposition` for local downloads;
- return signed URLs for S3-compatible storage when the storage adapter provides them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q tests/unit/test_batch_routes.py tests/unit/test_export_routes.py tests/integration/test_batch_upload_and_export.py`
Expected: batch and export API tests pass.

---

### Task 5: S3-compatible storage adapter and optional MinIO integration

**Files:**
- Create: `src/marriage_ocr_api/storage/s3.py`
- Create: `tests/unit/test_storage_s3.py`
- Create: `tests/integration/test_minio_storage.py`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `StorageService`, Boto3 client configuration, bucket/endpoint settings.
- Produces: S3-compatible `put_file`, `open_read`, `materialize`, `exists`, `delete`, and `signed_download_url`.

- [ ] **Step 1: Write the failing tests**

```python
def test_s3_storage_builds_signed_url(monkeypatch) -> None:
    storage = S3StorageService(
        bucket_name="exports",
        endpoint_url="http://minio:9000",
        region_name="us-east-1",
        access_key_id="test",
        secret_access_key="test",
    )
    assert storage.signed_download_url("exports/123/records.xlsx", 60) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_storage_s3.py`
Expected: missing adapter or failing S3 URL assertions.

- [ ] **Step 3: Implement the adapter**

Keep the adapter endpoint-driven so it works with MinIO and DigitalOcean Spaces without AWS hostname assumptions.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
pytest -q tests/unit/test_storage_s3.py
pytest -q tests/integration/test_minio_storage.py
```

Expected: S3 unit tests pass and the MinIO profile test passes when the profile is enabled.

---

### Task 6: OpenAPI export and frontend integration docs

**Files:**
- Create: `scripts/export_openapi.py`
- Create: `docs/frontend-integration.md`
- Modify: `README.md`
- Modify: `src/marriage_ocr_api/main.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the full API app and OpenAPI metadata.
- Produces: exported `artifacts/openapi.json`, frontend integration guidance, and stable schema/operation metadata.

- [ ] **Step 1: Write the failing checks**

```python
def test_export_openapi_script_writes_file(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, "scripts/export_openapi.py"], cwd=repo_root, check=False)
    assert result.returncode == 0
    assert (repo_root / "artifacts/openapi.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest -q tests/unit/test_openapi_export.py`
Expected: missing script or missing OpenAPI export artifact.

- [ ] **Step 3: Implement the docs and script**

Include:

- login and refresh flows;
- batch/document upload flow;
- export polling and download flow;
- the documented OpenAPI Generator or Orval command;
- request ID behavior and response schema stability.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
pytest -q tests/unit/test_openapi_export.py
python scripts/export_openapi.py
```

Expected: the script writes `artifacts/openapi.json` and the docs exist with the documented flows.

---

### Task 7: Full Stage 7 verification

**Files:**
- Modify: any files changed in Tasks 1-6 if verification or formatting requires cleanup

**Interfaces:**
- Consumes: the full batch/export/storage surface.
- Produces: a stable backend that can create batches, upload documents, generate exports, and serve local or signed downloads.

- [ ] **Step 1: Run the focused Stage 7 suites**

Run:
```bash
pytest -q tests/unit/test_batch_repository.py tests/unit/test_export_repository.py tests/unit/test_storage_local.py tests/unit/test_export_writer.py tests/unit/test_export_service.py tests/unit/test_batch_routes.py tests/unit/test_export_routes.py tests/unit/test_storage_s3.py
pytest -q tests/integration/test_batch_migration.py tests/integration/test_batch_upload_and_export.py tests/integration/test_minio_storage.py
```

- [ ] **Step 2: Run the backend regression suite**

Run:
```bash
pytest -q
ruff check .
mypy src
```

- [ ] **Step 3: Stop for review**

Report the exact commands and outputs before making any further changes outside Stage 7.
