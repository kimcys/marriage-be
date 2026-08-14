# Stage 8 Frontend Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the frontend-facing backend contract with explicit OpenAPI metadata, a Docker-only end-to-end smoke test, CI coverage, and integration docs.

**Architecture:** Keep the backend contract work inside `marriage-be`. Add explicit operation IDs and request examples to the existing FastAPI routes, then export the resulting schema through the checked-in script. Add one Docker-oriented smoke test that exercises the current container stack with the existing fake OCR fixture. Finish by wiring the same verification commands into CI and documenting the flow for Angular consumers.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Pytest, Docker Compose, GitHub Actions, Ruff, Mypy, OpenAPI JSON.

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

### Task 1: OpenAPI contract metadata

**Files:**
- Modify: `src/marriage_ocr_api/api/routers/jobs.py`
- Modify: `src/marriage_ocr_api/api/routers/__init__.py`
- Modify: `src/marriage_ocr_api/batches/routers.py`
- Modify: `src/marriage_ocr_api/exports/routers.py`
- Modify: `src/marriage_ocr_api/records/routers.py`
- Modify: `src/marriage_ocr_api/batches/response_models.py`
- Modify: `src/marriage_ocr_api/exports/response_models.py`
- Modify: `src/marriage_ocr_api/records/response_models.py`
- Test: `tests/unit/test_openapi_contract.py`
- Test: `tests/unit/test_openapi_export.py`

**Interfaces:**
- Consumes: FastAPI route decorators, Pydantic schemas, `app.openapi()`.
- Produces: explicit `operation_id` values and visible request examples for the frontend contract.

- [ ] **Step 1: Write the failing test**

```python
from marriage_ocr_api.main import app


def test_openapi_has_stable_operation_ids_and_examples() -> None:
    spec = app.openapi()
    assert spec["paths"]["/api/v1/batches"]["post"]["operationId"] == "create_batch"
    assert spec["paths"]["/api/v1/exports"]["post"]["operationId"] == "create_export"
    assert "examples" in spec["paths"]["/api/v1/batches"]["post"]["requestBody"]["content"]["application/json"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest -q tests/unit/test_openapi_contract.py`
Expected: missing operation IDs and/or missing schema examples.

- [ ] **Step 3: Implement the metadata**

Add `operation_id=` to the public route decorators. Add `json_schema_extra={"examples": [...]}` to the public request models that the Angular frontend will post.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest -q tests/unit/test_openapi_contract.py tests/unit/test_openapi_export.py`
Expected: stable OpenAPI metadata and export script pass.

---

### Task 2: Docker-only end-to-end smoke test

**Files:**
- Create: `tests/integration/test_docker_e2e.py`
- Create: `scripts/docker_e2e.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Docker Compose, the existing fake OCR fixture, the batch/upload/export APIs.
- Produces: a repeatable end-to-end smoke path that exercises the container stack.

- [ ] **Step 1: Write the failing test**

```python
def test_docker_e2e_smoke() -> None:
    result = subprocess.run([sys.executable, "scripts/docker_e2e.py"], cwd=repo_root, check=False)
    assert result.returncode == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest -q tests/integration/test_docker_e2e.py`
Expected: missing script or missing Docker smoke harness.

- [ ] **Step 3: Implement the smoke path**

The script should bring up the stack with Docker Compose, create a batch, upload a PDF, wait for the OCR job to finish using the fake OCR fixture, create an export, and verify the downloaded artifact exists and is non-empty.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest -q tests/integration/test_docker_e2e.py`
Expected: Docker-only end-to-end flow passes.

---

### Task 3: CI and docs

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/frontend-integration.md`
- Modify: `src/marriage_ocr_api/main.py`
- Modify: `scripts/export_openapi.py` if needed for deterministic output

**Interfaces:**
- Consumes: verification commands, generated OpenAPI schema, Docker smoke script.
- Produces: documented frontend usage and CI coverage for the full backend contract.

- [ ] **Step 1: Write the failing checks**

Add CI steps for OpenAPI export and the Docker smoke test.

- [ ] **Step 2: Run the checks to verify they fail**

Run: `ruff check .`, `mypy src`, `pytest -q`, `python scripts/export_openapi.py`, `pytest -q tests/integration/test_docker_e2e.py`

- [ ] **Step 3: Implement the docs and CI**

Document the Angular integration flow, request IDs, pagination, export download behavior, and the local verification commands.

- [ ] **Step 4: Run the checks to verify they pass**

Run the same command set again plus `docker compose config`.

---

### Task 4: Full Stage 8 verification

**Files:**
- All Stage 8 files above

**Interfaces:**
- Consumes: the completed backend contract, CI, docs, and Docker smoke harness.
- Produces: a verified Stage 8 deliverable.

- [ ] **Step 1: Run the full backend suite**

Run: `pytest -q`

- [ ] **Step 2: Run static checks**

Run: `ruff check .`
Run: `mypy src`

- [ ] **Step 3: Run container verification**

Run: `docker compose config`

- [ ] **Step 4: Run OpenAPI export**

Run: `python scripts/export_openapi.py`

