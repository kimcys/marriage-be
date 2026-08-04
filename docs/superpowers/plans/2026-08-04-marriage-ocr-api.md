# Marriage OCR API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 FastAPI backend that accepts uploads, persists OCR jobs, and runs the upstream `marriage-ocr` CLI in a bounded subprocess executor.

**Architecture:** Keep the API, database, storage, and subprocess runner separated by small service boundaries. The API layer only parses HTTP and returns responses; the service layer owns orchestration; the repository layer owns SQLAlchemy persistence; the storage layer owns upload validation and safe filesystem writes; the runner owns subprocess execution; the executor owns background lifecycle.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2.x sync, Psycopg 3, Alembic, PostgreSQL 16, python-multipart, filetype, HTTPX, Pytest, Ruff, Mypy, Docker Compose.

## Global Constraints

- Repository name is `marriage-ocr-api`.
- Backend only; no Angular code.
- Do not copy the upstream OCR source tree.
- Do not import `marriage_ocr.pipeline.process_input()`; use the CLI subprocess boundary only.
- Launch the OCR CLI with an argument list and `shell=False`.
- Use Python 3.12.
- Keep FastAPI below `1.0`, SQLAlchemy below `3.0`, Pydantic below `3.0`, Psycopg below `4.0`, Alembic below `2.0`, and HTTPX below `1.0`.
- Use UUIDs for public job identifiers.
- Store timestamps in UTC.
- Run only one API process and one OCR job at a time in Phase 1.
- Tests must not call Google Vision or Gemini.
- Tests must not require the real upstream OCR package.

---

### Task 1: Project foundation, settings, and liveness

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/marriage_ocr_api/__init__.py`
- Create: `src/marriage_ocr_api/main.py`
- Create: `src/marriage_ocr_api/api/__init__.py`
- Create: `src/marriage_ocr_api/api/errors.py`
- Create: `src/marriage_ocr_api/api/dependencies.py`
- Create: `src/marriage_ocr_api/api/routers/__init__.py`
- Create: `src/marriage_ocr_api/api/routers/health.py`
- Create: `src/marriage_ocr_api/core/__init__.py`
- Create: `src/marriage_ocr_api/core/config.py`
- Create: `src/marriage_ocr_api/core/logging.py`
- Create: `src/marriage_ocr_api/core/request_id.py`
- Create: `src/marriage_ocr_api/db/__init__.py`
- Create: `src/marriage_ocr_api/db/session.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_health_routes.py`
- Create: `tests/unit/test_request_id.py`

**Interfaces:**
- Consumes: `Settings`, `create_app()`, request ID middleware, `/health` route.
- Produces: app factory, cached settings, request ID propagation, liveness response schema, and a test harness for later tasks.

- [ ] **Step 1: Write the failing tests**
  - `tests/unit/test_config.py` should assert that `CORS_ORIGINS` becomes a list, `MAX_UPLOAD_BYTES` must be positive, and `OCR_MAX_CONCURRENT_JOBS` rejects any value other than `1`.
  - `tests/unit/test_health_routes.py` should assert `GET /health` returns `200` and `{"status":"ok","service":"Marriage OCR API"}` without a database.
  - `tests/unit/test_request_id.py` should assert an incoming valid UUID `X-Request-ID` is echoed back and invalid input is replaced with a generated UUID.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_config.py tests/unit/test_health_routes.py tests/unit/test_request_id.py`
  - Expected: import or assertion failures because the package does not exist yet.

- [ ] **Step 3: Implement the minimal foundation**
  - Add the settings model, request ID middleware, logging helpers, app factory, and health router.
  - Keep the health route independent of database access.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_config.py tests/unit/test_health_routes.py tests/unit/test_request_id.py`
  - Expected: all three tests pass.

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: scaffold FastAPI service and health endpoint`

### Task 2: Database model, repository, and migration

**Files:**
- Create: `src/marriage_ocr_api/db/base.py`
- Create: `src/marriage_ocr_api/db/models.py`
- Create: `src/marriage_ocr_api/db/repositories.py`
- Modify: `src/marriage_ocr_api/db/session.py`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_create_ocr_jobs.py`
- Create: `alembic.ini`
- Create: `tests/unit/test_job_repository.py`
- Create: `tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: SQLAlchemy `Session`, `OCRJob` model, `JobStatus`.
- Produces: repository CRUD/state-transition methods, Alembic migration, migration bootstrap.

- [ ] **Step 1: Write the failing tests**
  - Repository tests should cover creating a job, marking it processing/completed/failed, and rejecting invalid transitions.
  - Migration test should upgrade from empty DB, verify `ocr_jobs` plus required indexes, then downgrade back to base and verify removal.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_job_repository.py tests/integration/test_migrations.py`

- [ ] **Step 3: Implement the minimal persistence layer**
  - Add the SQLAlchemy model and repository methods with conditional updates or row locking.
  - Configure Alembic to read `DATABASE_URL` from settings without leaking passwords.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_job_repository.py tests/integration/test_migrations.py`

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: add persistent OCR job model`

### Task 3: Local storage and upload validation

**Files:**
- Create: `src/marriage_ocr_api/storage/__init__.py`
- Create: `src/marriage_ocr_api/storage/local.py`
- Create: `src/marriage_ocr_api/jobs/paths.py`
- Create: `tests/unit/test_file_validation.py`
- Create: `tests/unit/test_job_paths.py`

**Interfaces:**
- Consumes: `Settings`, job UUID, `UploadFile`-like object, safe path helpers.
- Produces: chunked upload validation, atomic rename, relative path outputs, cleanup on failure.

- [ ] **Step 1: Write the failing tests**
  - Tests should accept valid PDF/JPEG/PNG/TIFF signatures, reject unsupported extensions and mismatched signatures, enforce upload size limits, and ensure generated paths cannot escape `STORAGE_ROOT`.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_file_validation.py tests/unit/test_job_paths.py`

- [ ] **Step 3: Implement the storage module**
  - Stream uploads to `.part`, validate signature with `filetype` plus a `%PDF-` check, rename atomically, and delete partials on failure.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_file_validation.py tests/unit/test_job_paths.py`

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: add validated local upload storage`

### Task 4: Job API, schemas, and standard errors

**Files:**
- Create: `src/marriage_ocr_api/jobs/__init__.py`
- Create: `src/marriage_ocr_api/jobs/schemas.py`
- Create: `src/marriage_ocr_api/jobs/status.py`
- Create: `src/marriage_ocr_api/jobs/service.py`
- Create: `src/marriage_ocr_api/api/routers/jobs.py`
- Modify: `src/marriage_ocr_api/api/errors.py`
- Create: `tests/unit/test_job_routes.py`

**Interfaces:**
- Consumes: repository methods, storage layer, app state executor hook.
- Produces: create/list/get/download endpoints, error schema, job response schema.

- [ ] **Step 1: Write the failing tests**
  - Tests should cover upload `202` with `Location`, list pagination/filtering, unknown job `404`, download pre-completion `409`, and completed download headers.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_job_routes.py`

- [ ] **Step 3: Implement the job schemas and routes**
  - Keep filesystem paths out of API responses and normalize errors into the shared shape.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_job_routes.py`

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: add OCR job REST API`

### Task 5: Subprocess runner

**Files:**
- Create: `src/marriage_ocr_api/jobs/runner.py`
- Create: `tests/fixtures/fake_ocr_cli.py`
- Create: `tests/unit/test_job_runner.py`
- Create: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: typed run request, settings, job paths.
- Produces: command construction, timeout handling, log redirection, success/failure classification.

- [ ] **Step 1: Write the failing tests**
  - Tests should verify `shell=False`, the exact argument sequence, success only when a non-empty XLSX exists, and failure mapping for non-zero exit, timeout, and missing output.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_job_runner.py tests/integration/test_job_lifecycle.py`

- [ ] **Step 3: Implement the runner and fake CLI**
  - Run the upstream CLI in a subprocess and use the fake CLI for lifecycle testing.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_job_runner.py tests/integration/test_job_lifecycle.py`

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: execute OCR through isolated CLI subprocess`

### Task 6: Executor, startup recovery, and lifecycle integration

**Files:**
- Create: `src/marriage_ocr_api/jobs/executor.py`
- Modify: `src/marriage_ocr_api/main.py`
- Create: `tests/unit/test_job_startup_recovery.py`

**Interfaces:**
- Consumes: job service, repository, runner, app lifespan.
- Produces: single-worker background execution and stale processing recovery.

- [ ] **Step 1: Write the failing tests**
  - Cover recovery marking `PROCESSING` jobs as `FAILED` on startup and verify the executor opens its own session in the background thread.

- [ ] **Step 2: Run the tests to verify they fail**
  - Run: `pytest -q tests/unit/test_job_startup_recovery.py`

- [ ] **Step 3: Implement the executor and lifespan wiring**
  - Submit jobs to one worker and mark interrupted jobs failed during startup.

- [ ] **Step 4: Run the tests to verify they pass**
  - Run: `pytest -q tests/unit/test_job_startup_recovery.py`

- [ ] **Step 5: Commit**
  - Suggested commit message: `feat: process OCR jobs in bounded background executor`

### Task 7: Readiness checks, Docker, and developer workflow

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `Makefile`
- Create: `.github/workflows/ci.yml`
- Update: `README.md`

**Interfaces:**
- Consumes: settings, app factory, database migration command, container runtime.
- Produces: local PostgreSQL stack, readiness endpoint, install/lint/type/test commands.

- [ ] **Step 1: Write the failing tests or config checks**
  - Add readiness tests for PostgreSQL, storage, OCR config, and OCR python path.
  - Add a `docker compose config`-friendly compose file and a buildable Dockerfile.

- [ ] **Step 2: Run the commands to verify they fail**
  - Run: `docker compose config`, `docker compose build api`, and the relevant readiness tests.

- [ ] **Step 3: Implement the workflow files and container setup**
  - Pin the upstream OCR repo to the resolved commit SHA and keep the checkout at `/opt/marriage-ocr`.

- [ ] **Step 4: Run the commands to verify they pass**
  - Run: `docker compose config`, `docker compose build api`, `docker compose up -d postgres`, `docker compose run --rm api alembic upgrade head`.

- [ ] **Step 5: Commit**
  - Suggested commit message: `build: add reproducible local PostgreSQL stack`

### Task 8: CI, documentation, and full verification

**Files:**
- Create: `.github/workflows/ci.yml`
- Update: `README.md`

**Interfaces:**
- Consumes: all application code and workflow commands.
- Produces: CI checks, usage docs, troubleshooting notes, and future migration guidance.

- [ ] **Step 1: Review coverage against the prompt**
  - Confirm every required endpoint, response shape, migration, and test type has a corresponding implementation.

- [ ] **Step 2: Run the full verification commands**
  - Run: `ruff format --check .`
  - Run: `ruff check .`
  - Run: `mypy src`
  - Run: `pytest -q`
  - Run: `alembic upgrade head`
  - Run: `alembic downgrade base`
  - Run: `alembic upgrade head`
  - Run: `docker compose config`
  - Run: `docker compose build api`
  - Run: `docker compose up -d postgres`
  - Run: `docker compose run --rm api alembic upgrade head`
  - Run: `docker compose run --rm api pytest -q`
  - Run: `docker compose down -v`

- [ ] **Step 3: Finalize docs and CI**
  - Document the Phase 1 durability limitation and the future Valkey/Celery + DigitalOcean Spaces migration path.

- [ ] **Step 4: Commit**
  - Suggested commit message: `docs: document backend setup and OCR integration`

