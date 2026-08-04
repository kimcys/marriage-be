# Codex Build Prompt: Marriage OCR FastAPI Backend — Phase 1

> **For Codex:** Work directly in the current repository. If it is empty, initialize it as `marriage-ocr-api`. Do not create a ZIP archive. Do not create a monorepo. Implement and verify the backend in small, reviewable commits.
>
> Use test-driven development. Before claiming completion, run the complete test, lint, migration, and container smoke-test commands described below and report the actual results.

## 1. Project goal

Build a new, standalone repository named `marriage-ocr-api` that provides a FastAPI backend for the existing OCR engine at:

- `https://github.com/kimcys/marriage-ocr`

The upstream `marriage-ocr` repository remains separate and must not be copied into this repository or modified as part of this work.

The backend must:

1. Accept one PDF or image upload.
2. Save the uploaded file in a job-specific local directory.
3. Create and persist an OCR job in PostgreSQL.
4. Return `202 Accepted` without waiting for OCR to finish.
5. Run the upstream OCR CLI in a separate subprocess.
6. Persist job status, timestamps, output paths, and sanitized failure information.
7. Let clients list jobs, inspect a job, and download the generated XLSX file.
8. Expose OpenAPI documentation suitable for a later Angular frontend.

This is the first development milestone. The API contract should be shaped for future asynchronous workers, but this milestone must not add Valkey, Redis, Celery, RabbitMQ, DigitalOcean Spaces, Kubernetes, authentication, batch upload, or Angular code.

## 2. Source context

Inspect the upstream repository before implementation. Treat its current README, CLI arguments, production configuration, and package metadata as the source of truth.

The currently documented processing command is equivalent to:

```bash
python -m marriage_ocr.cli process \
  --input <input-file-or-directory> \
  --output <output-xlsx> \
  --debug <debug-directory> \
  --config <production-yaml> \
  --reset-output
```

The upstream production configuration allows these input extensions:

```text
.jpg
.jpeg
.png
.tif
.tiff
.pdf
```

The upstream package requires Python 3.10 or newer and uses Google Vision as its default OCR engine. A valid `GOOGLE_APPLICATION_CREDENTIALS` value is needed for real OCR. `GEMINI_API_KEY` is optional and must be passed through when configured.

Do not import `marriage_ocr.pipeline.process_input()` in the API. The integration boundary for Phase 1 is the CLI subprocess.

## 3. Chosen architecture

Use this architecture:

```text
Client / future Angular frontend
              |
              | HTTP multipart + JSON
              v
        FastAPI application
              |
       PostgreSQL job table
              |
   bounded in-process executor
       maximum concurrency: 1
              |
              | subprocess, shell=False
              v
 python -m marriage_ocr.cli process
              |
      local job storage
 input / output / debug / logs
```

### Why this design

- The API and OCR engine remain in separate repositories.
- A crash or heavy memory use in OCR is isolated from the FastAPI interpreter.
- The HTTP API already returns a job and supports polling.
- A future Celery worker can replace the in-process executor without redesigning the endpoints or database model.
- The first milestone remains small enough to run locally with PostgreSQL and Docker Compose.

### Phase 1 limitation that must be documented

The in-process executor is not a durable production queue. If the API process restarts, queued or running work can be interrupted. On startup, the application must mark stale `PROCESSING` jobs as `FAILED` with error code `PROCESS_INTERRUPTED` and a safe message. Run one Uvicorn worker only in Phase 1.

## 4. Required technology

Use:

- Python 3.12
- FastAPI
- Uvicorn
- Pydantic v2 and `pydantic-settings`
- SQLAlchemy 2.x, synchronous API
- Psycopg 3
- Alembic
- PostgreSQL 16 in Docker Compose
- `python-multipart`
- `filetype` for file-signature validation
- Pytest
- HTTPX test client
- Ruff
- Mypy

Use a PEP 621 `pyproject.toml`. Do not use Poetry. A lock file may be created with `uv` if `uv` is available, but the project must also be installable with standard `pip install -e '.[dev]'`.

Use compatible version ranges rather than depending on unbounded latest versions. Keep FastAPI below `1.0`, SQLAlchemy below `3.0`, Pydantic below `3.0`, Psycopg below `4.0`, Alembic below `2.0`, and HTTPX below `1.0`.

## 5. Global constraints

- The repository name is `marriage-ocr-api`.
- This repository contains backend code only.
- Do not include the Angular frontend.
- Do not copy the upstream OCR source tree.
- Do not edit the upstream OCR repository.
- Do not use direct Python imports from the upstream OCR pipeline.
- Launch the CLI with an argument list and `shell=False`.
- Never construct a shell command by concatenating user input.
- Never use the original filename as a filesystem path.
- Do not put Google credentials, Gemini keys, uploaded documents, output documents, debug artifacts, or subprocess logs in Git.
- Do not expose absolute server paths in API responses.
- Do not expose raw stack traces or complete subprocess stderr to API clients.
- Store timestamps in UTC.
- Use UUIDs for public job identifiers.
- Run only one API process and one OCR job at a time in Phase 1.
- Tests must not call Google Vision or Gemini.
- Tests must not require the real upstream OCR package.
- Keep modules focused; avoid large service or router files.

## 6. Required repository structure

Create this structure. Small adjustments are acceptable only when they preserve the same responsibilities and are explained in the final report.

```text
marriage-ocr-api/
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── README.md
├── alembic.ini
├── pyproject.toml
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_ocr_jobs.py
├── src/
│   └── marriage_ocr_api/
│       ├── __init__.py
│       ├── main.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── dependencies.py
│       │   ├── errors.py
│       │   └── routers/
│       │       ├── __init__.py
│       │       ├── health.py
│       │       └── jobs.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── logging.py
│       │   └── request_id.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── models.py
│       │   ├── repositories.py
│       │   └── session.py
│       ├── jobs/
│       │   ├── __init__.py
│       │   ├── executor.py
│       │   ├── paths.py
│       │   ├── runner.py
│       │   ├── schemas.py
│       │   ├── service.py
│       │   └── status.py
│       └── storage/
│           ├── __init__.py
│           └── local.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── fixtures/
    │   ├── __init__.py
    │   ├── sample.jpg
    │   └── fake_ocr_cli.py
    ├── integration/
    │   ├── test_job_lifecycle.py
    │   └── test_migrations.py
    └── unit/
        ├── test_config.py
        ├── test_file_validation.py
        ├── test_job_repository.py
        ├── test_job_routes.py
        ├── test_job_runner.py
        └── test_job_startup_recovery.py
```

Do not commit generated `storage/`, test databases, caches, credentials, or real OCR outputs.

## 7. Configuration contract

Implement a cached `Settings` class using `pydantic-settings`. Use environment variables with these names and defaults:

```dotenv
APP_NAME=Marriage OCR API
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
APP_LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
CORS_ORIGINS=http://localhost:4200
DATABASE_URL=postgresql+psycopg://marriage_ocr:marriage_ocr@postgres:5432/marriage_ocr
STORAGE_ROOT=/app/storage
MAX_UPLOAD_BYTES=104857600
UPLOAD_CHUNK_BYTES=1048576
OCR_PYTHON_EXECUTABLE=/usr/local/bin/python
OCR_MODULE=marriage_ocr.cli
OCR_CONFIG_PATH=/opt/marriage-ocr/config/production.yaml
OCR_TIMEOUT_SECONDS=3600
OCR_MAX_CONCURRENT_JOBS=1
OCR_STDERR_API_LIMIT=1000
MARRIAGE_OCR_GIT_URL=https://github.com/kimcys/marriage-ocr.git
MARRIAGE_OCR_GIT_REF=RESOLVE_WITH_GIT_LS_REMOTE_BEFORE_COMMIT
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-vision.json
GEMINI_API_KEY=
```

During implementation, resolve the current upstream `main` commit SHA and run `git ls-remote https://github.com/kimcys/marriage-ocr.git refs/heads/main`, take the first 40-character SHA from that command, and write that exact SHA into `.env.example`, Docker build defaults, and documentation. The sentinel `RESOLVE_WITH_GIT_LS_REMOTE_BEFORE_COMMIT` must not remain in committed files. Do not leave the Docker build pinned to a floating branch.

Parsing requirements:

- `CORS_ORIGINS` must become a list of explicit origins.
- Wildcard CORS is not permitted.
- `MAX_UPLOAD_BYTES` must be positive.
- `OCR_TIMEOUT_SECONDS` must be positive.
- `OCR_MAX_CONCURRENT_JOBS` must equal `1` in Phase 1; reject other values with a clear startup configuration error.
- `STORAGE_ROOT` and `OCR_CONFIG_PATH` must be represented as `pathlib.Path` values.

## 8. Database model

Create one table named `ocr_jobs`.

Use SQLAlchemy's portable `Uuid` type with Python `uuid.UUID` values. Store status as a string column rather than a PostgreSQL native enum so later status changes do not require enum DDL operations.

Required columns:

```text
id                       UUID primary key
status                   varchar(32), indexed, not null
original_filename        varchar(255), not null
stored_filename          varchar(255), not null
content_type             varchar(100), not null
file_size_bytes          bigint, not null
input_relative_path      varchar(500), not null
output_relative_path     varchar(500), nullable
debug_relative_path      varchar(500), not null
stdout_log_relative_path varchar(500), not null
stderr_log_relative_path varchar(500), not null
ocr_git_ref              varchar(100), not null
error_code               varchar(100), nullable
error_message            varchar(1000), nullable
created_at               timestamptz, not null
updated_at               timestamptz, not null
started_at               timestamptz, nullable
completed_at             timestamptz, nullable
```

Job statuses:

```python
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
```

State transition rules:

```text
new job       -> PENDING
PENDING       -> PROCESSING
PROCESSING    -> COMPLETED
PROCESSING    -> FAILED
PENDING       -> FAILED only when executor submission fails
```

Do not create record-level OCR tables in Phase 1.

## 9. Filesystem contract

Use generated paths only. For a job ID `123e4567-e89b-12d3-a456-426614174000`, use:

```text
storage/jobs/123e4567-e89b-12d3-a456-426614174000/
├── input/
│   └── source.pdf
├── output/
│   └── result.xlsx
├── debug/
└── logs/
    ├── stdout.log
    └── stderr.log
```

Rules:

- Preserve the validated file extension, not the user-provided basename.
- Store the original filename only as metadata.
- Use `source.<extension>` as the stored input name.
- Resolve and verify every generated path remains under `STORAGE_ROOT`.
- Save uploads in chunks; do not read the full file into memory.
- Write first to `source.<extension>.part`, validate size and signature, then atomically rename it.
- Remove partial files and the incomplete job directory when upload validation fails before the job is committed.
- Database paths must be relative to `STORAGE_ROOT`.
- API responses must use URLs and metadata, not filesystem paths.

## 10. Upload validation

Accept one multipart field named `file`.

Allowed extensions and detected media types:

```text
.pdf   application/pdf
.jpg   image/jpeg
.jpeg  image/jpeg
.png   image/png
.tif   image/tiff
.tiff  image/tiff
```

Validation sequence:

1. Reject a missing or empty filename with `400`.
2. Normalize the extension to lowercase.
3. Reject an extension outside the allowlist with `415`.
4. Stream the upload to a temporary file while counting bytes.
5. If the byte count exceeds `MAX_UPLOAD_BYTES`, stop writing, delete the partial file, and return `413`.
6. Reject a zero-byte file with `400`.
7. Detect the real file type from its signature using `filetype` and a direct `%PDF-` check for PDFs.
8. Reject an extension/signature mismatch with `415`.
9. Do not trust the browser-supplied `Content-Type`; record the detected content type.
10. Rename the temporary file atomically only after validation succeeds.

Use a custom exception type from the storage layer and map it to the API error schema.

## 11. Subprocess runner contract

Implement a dedicated `SubprocessOCRRunner`. It must not depend on FastAPI.

Use a typed request object similar to:

```python
@dataclass(frozen=True)
class OCRRunRequest:
    input_path: Path
    output_path: Path
    debug_path: Path
    stdout_log_path: Path
    stderr_log_path: Path
```

Use a typed result object similar to:

```python
@dataclass(frozen=True)
class OCRRunResult:
    return_code: int
    timed_out: bool
    duration_seconds: float
```

Build exactly this logical argument sequence from trusted settings and generated paths:

```python
[
    settings.ocr_python_executable,
    "-m",
    settings.ocr_module,
    "process",
    "--input",
    str(request.input_path),
    "--output",
    str(request.output_path),
    "--debug",
    str(request.debug_path),
    "--config",
    str(settings.ocr_config_path),
    "--reset-output",
]
```

Execution requirements:

- Use `subprocess.Popen` or `subprocess.run` with an argument list.
- Set `shell=False` explicitly.
- Set the subprocess working directory to the upstream repository root when available; otherwise use a configurable safe working directory.
- Redirect stdout and stderr directly to the job log files instead of holding unbounded output in memory.
- Pass the current environment so Google and Gemini credentials reach the subprocess.
- Enforce `OCR_TIMEOUT_SECONDS`.
- On Linux, start a new process session. On timeout, terminate the process group, wait briefly, then kill it if needed.
- Return structured execution metadata to the job service.
- Never parse Rich-formatted console output to determine success.
- A run is successful only when the exit code is zero and `result.xlsx` exists as a non-empty regular file under the job output directory.
- When the exit code is non-zero, use error code `OCR_PROCESS_FAILED`.
- When the timeout is exceeded, use error code `OCR_PROCESS_TIMEOUT`.
- When exit code is zero but the expected output is missing or empty, use error code `OCR_OUTPUT_MISSING`.
- Keep full stdout and stderr only in the job's local log files.
- Store at most the final `OCR_STDERR_API_LIMIT` characters of sanitized stderr in `error_message`.
- Remove ANSI escape sequences before storing an error message.
- Do not include credentials or environment dumps in logs.

The runner must be easy to replace with a fake in tests.

## 12. Executor and lifecycle

Create a bounded `JobExecutor` backed by `concurrent.futures.ThreadPoolExecutor` with exactly one worker.

Responsibilities:

- Accept a job UUID after the upload transaction commits.
- Submit a callable that opens its own database session.
- Prevent the request-scoped SQLAlchemy session from being reused in the background thread.
- Call the job service to transition `PENDING -> PROCESSING`.
- Run the OCR subprocess.
- Mark the job `COMPLETED` or `FAILED` in a fresh transaction.
- Log unexpected exceptions and store `INTERNAL_PROCESSING_ERROR` with a safe message.

Create the executor in the FastAPI lifespan context and store it on `app.state`. Shut it down during application shutdown without pretending outstanding work is durable.

At startup, execute a recovery query:

```text
UPDATE ocr_jobs
SET status = 'FAILED',
    error_code = 'PROCESS_INTERRUPTED',
    error_message = 'OCR processing was interrupted by an application restart.',
    completed_at = current UTC time,
    updated_at = current UTC time
WHERE status = 'PROCESSING';
```

Implement this through the repository/service layer, not raw SQL in `main.py`.

## 13. API contract

Use `/api/v1` as the version prefix.

### `GET /health`

Purpose: liveness only.

Response `200`:

```json
{
  "status": "ok",
  "service": "Marriage OCR API"
}
```

Do not perform database or OCR checks in liveness.

### `GET /ready`

Check:

- PostgreSQL responds to `SELECT 1`.
- `STORAGE_ROOT` exists and is writable.
- `OCR_CONFIG_PATH` exists and is a regular file.
- `OCR_PYTHON_EXECUTABLE` exists and is executable.

Response `200` when all checks pass:

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "storage": "ok",
    "ocr_config": "ok",
    "ocr_python": "ok"
  }
}
```

Response `503` when any check fails. Do not reveal secrets or credential contents.

### `POST /api/v1/jobs`

Multipart input:

```text
file: UploadFile
```

Behavior:

1. Create a UUID.
2. Build safe job paths.
3. Validate and save the file.
4. Create the database row with `PENDING`.
5. Commit.
6. Submit the job ID to the executor.
7. Return `202 Accepted`.
8. Set the `Location` header to `/api/v1/jobs/{job_id}`.

Response:

```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "PENDING",
  "original_filename": "register.pdf",
  "content_type": "application/pdf",
  "file_size_bytes": 123456,
  "created_at": "2026-08-04T03:00:00Z",
  "updated_at": "2026-08-04T03:00:00Z",
  "started_at": null,
  "completed_at": null,
  "error": null,
  "links": {
    "self": "/api/v1/jobs/123e4567-e89b-12d3-a456-426614174000",
    "download": null
  }
}
```

### `GET /api/v1/jobs`

Query parameters:

```text
status: optional JobStatus
limit: integer, default 20, minimum 1, maximum 100
offset: integer, default 0, minimum 0
```

Sort newest first by `created_at`, then by `id` for deterministic ordering.

Response:

```json
{
  "items": [],
  "limit": 20,
  "offset": 0,
  "total": 0
}
```

### `GET /api/v1/jobs/{job_id}`

Return the job response above.

Return `404` with the standard error body when the UUID does not exist.

### `GET /api/v1/jobs/{job_id}/download`

Rules:

- Return `404` when the job does not exist.
- Return `409 JOB_NOT_COMPLETED` when status is not `COMPLETED`.
- Return `410 OUTPUT_FILE_MISSING` when the database says completed but the file no longer exists.
- Return an XLSX `FileResponse` when available.
- Download filename: `<sanitized-original-stem>-result.xlsx`.
- Use content type `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
- Add `X-Content-Type-Options: nosniff`.

Do not add an endpoint that exposes raw stdout or stderr in Phase 1.

## 14. Error response contract

All expected API errors must use:

```json
{
  "error": {
    "code": "UNSUPPORTED_FILE_TYPE",
    "message": "Only PDF, JPEG, PNG, and TIFF files are supported.",
    "request_id": "f2c73976-2f39-46e9-bc79-376475fef45f"
  }
}
```

Required codes:

```text
INVALID_UPLOAD
EMPTY_FILE
UPLOAD_TOO_LARGE
UNSUPPORTED_FILE_TYPE
FILE_SIGNATURE_MISMATCH
JOB_NOT_FOUND
JOB_NOT_COMPLETED
OUTPUT_FILE_MISSING
DATABASE_UNAVAILABLE
SERVICE_NOT_READY
INTERNAL_ERROR
```

Validation errors generated by FastAPI/Pydantic must be converted into the same top-level shape with code `REQUEST_VALIDATION_ERROR`. Include concise field-level details without returning Python tracebacks.

Add or preserve an `X-Request-ID` header. Accept a valid incoming UUID request ID; otherwise generate a new UUID. Include it in logs and error responses.

## 15. Response schemas

Use Pydantic models. Do not return SQLAlchemy objects directly.

Use a nested safe error representation:

```python
class JobError(BaseModel):
    code: str
    message: str
```

Use a nested links representation:

```python
class JobLinks(BaseModel):
    self: str
    download: str | None
```

For completed jobs, `links.download` must be populated. For other statuses it must be `null`.

Never include these database fields in the API:

```text
input_relative_path
output_relative_path
debug_relative_path
stdout_log_relative_path
stderr_log_relative_path
```

## 16. Repository and service boundaries

### `db/repositories.py`

Provide small repository methods with explicit inputs and outputs:

```python
create_job(...)
get_job(job_id)
list_jobs(status, limit, offset)
count_jobs(status)
mark_processing(job_id, started_at)
mark_completed(job_id, output_relative_path, completed_at)
mark_failed(job_id, error_code, error_message, completed_at)
fail_interrupted_jobs(completed_at)
```

Use row locking or conditional updates when changing status so invalid transitions do not silently succeed.

### `jobs/service.py`

Own use-case orchestration:

```python
create_and_submit_job(upload, session, executor) -> OCRJob
process_job(job_id) -> None
get_job_or_raise(job_id, session) -> OCRJob
list_jobs(...) -> PaginatedJobs
build_job_response(job) -> JobResponse
```

Do not place SQL queries, file-signature logic, or subprocess mechanics in the API router.

### `storage/local.py`

Own upload streaming, signature validation, atomic rename, safe file resolution, and file deletion on validation failure.

### `jobs/runner.py`

Own the subprocess command and timeout behavior only.

### `jobs/executor.py`

Own bounded background execution and thread lifecycle only.

### `api/routers/jobs.py`

Own HTTP parsing, status codes, headers, and response conversion only.

## 17. Database migration

Create an Alembic migration named `0001_create_ocr_jobs.py` that creates the complete table and indexes.

Required indexes:

```text
ix_ocr_jobs_status
ix_ocr_jobs_created_at
```

The downgrade must remove indexes and the table cleanly.

Configure Alembic to read `DATABASE_URL` through the same settings class without logging database passwords.

## 18. Application setup

Create the app through a factory:

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    ...
```

Requirements:

- Register lifespan management.
- Register request ID middleware.
- Register CORS with explicit configured origins.
- Register error handlers.
- Include health and job routers.
- Set title, description, version, and tags in OpenAPI.
- Disable wildcard hosts only if a proper allowed-host setting is introduced; do not invent an incomplete security setting.
- Keep `/docs`, `/redoc`, and `/openapi.json` enabled in Phase 1.

Set the module-level application for Uvicorn:

```python
app = create_app()
```

## 19. Docker requirements

### Dockerfile

Use a multi-stage build when practical.

The final image must:

1. Use Python 3.12 slim.
2. Install only required OS packages.
3. Create a non-root application user.
4. Clone the upstream OCR repository during build using:
   - `ARG MARRIAGE_OCR_GIT_URL`
   - `ARG MARRIAGE_OCR_GIT_REF`
5. Check out the exact resolved commit SHA.
6. Install the upstream package.
7. Keep the upstream checkout at `/opt/marriage-ocr` so its `config/production.yaml` remains available.
8. Install this API package.
9. Create `/app/storage` with ownership assigned to the application user.
10. Run Uvicorn with one worker.
11. Never copy credentials or `.env` into the image.

The command should be equivalent to:

```bash
uvicorn marriage_ocr_api.main:app --host 0.0.0.0 --port 8000 --workers 1
```

### Docker Compose

Create services:

```text
api
postgres
```

PostgreSQL requirements:

```text
image: postgres:16
healthcheck: pg_isready
named volume for data
non-default application database/user/password supplied from environment
```

API requirements:

- Wait for healthy PostgreSQL.
- Run `alembic upgrade head` before Uvicorn.
- Persist `./storage` as `/app/storage` for local development.
- Expose port `8000`.
- Pass `GOOGLE_APPLICATION_CREDENTIALS` and optional `GEMINI_API_KEY` through environment configuration.
- Mount the Google service-account JSON read-only through a path configured outside Git.
- Do not fail the entire repository test suite merely because real Google credentials are absent.

Document a credentials-free smoke path using mocked tests and health endpoints. Real OCR testing is an explicit manual step.

## 20. Testing requirements

Use dependency injection so tests replace the real executor and runner.

### Unit tests

Cover at minimum:

1. Settings parse CORS origins and reject invalid concurrency.
2. Upload accepts valid PDF, JPEG, PNG, and TIFF signatures.
3. Upload rejects unsupported extensions.
4. Upload rejects mismatched signatures.
5. Upload stops and deletes the partial file when size exceeds the limit.
6. Generated paths cannot escape `STORAGE_ROOT`.
7. Subprocess command is an argument list and uses `shell=False`.
8. Runner handles success only when a non-empty XLSX exists.
9. Runner maps non-zero exit, timeout, and missing output correctly.
10. ANSI escape sequences are removed from stored error messages.
11. Repository enforces expected status transitions.
12. Startup recovery marks `PROCESSING` jobs failed.
13. API error responses contain `code`, `message`, and `request_id`.
14. Database path fields never appear in job responses.

### API tests

Cover at minimum:

1. `GET /health` returns `200` without a database dependency.
2. `GET /ready` returns `200` when checks pass.
3. `GET /ready` returns `503` when a check fails.
4. Valid upload returns `202`, a UUID, and a `Location` header.
5. Invalid upload produces the standard error body.
6. List pagination and status filter work.
7. Unknown job returns `404`.
8. Download before completion returns `409`.
9. Completed job download returns XLSX with the correct headers.
10. Completed job with missing output returns `410`.

### Integration test with fake CLI

Create `tests/fixtures/fake_ocr_cli.py`. It must support controlled modes through an environment variable or argument:

```text
success      -> writes a small valid XLSX and exits 0
failure      -> writes an ANSI-colored stderr message and exits non-zero
no-output    -> exits 0 without creating XLSX
timeout      -> sleeps longer than the test timeout
```

Use this fake executable to test the complete lifecycle without importing or calling the real OCR engine:

```text
POST job
-> PENDING
-> PROCESSING
-> COMPLETED or FAILED
-> GET job
-> optional download
```

Poll with a bounded test deadline. Do not add arbitrary long sleeps.

### Migration test

Start from an empty PostgreSQL database, run `alembic upgrade head`, inspect that the table and indexes exist, then run `alembic downgrade base` and verify removal.

## 21. Quality commands

Configure the project so these commands work:

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy src
pytest -q
alembic upgrade head
alembic downgrade base
alembic upgrade head
docker compose config
docker compose build api
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose run --rm api pytest -q
docker compose down -v
```

Add useful Make targets:

```text
install
format
lint
typecheck
test
migrate
run
docker-up
docker-down
verify
```

`make verify` must run formatting check, lint, type checking, and tests.

## 22. CI requirements

Add a GitHub Actions workflow at `.github/workflows/ci.yml`.

It must:

- Run on pushes and pull requests.
- Use Python 3.12.
- Start PostgreSQL 16 as a service.
- Install dev dependencies.
- Run Ruff formatting check.
- Run Ruff lint.
- Run Mypy.
- Run Alembic upgrade.
- Run Pytest.
- Never require Google or Gemini credentials.
- Never run the real OCR CLI in CI.

## 23. README requirements

Write a practical README with:

1. Architecture and repository boundary.
2. Explanation that `marriage-ocr` is installed and executed as a separate CLI subprocess.
3. Phase 1 durability limitation.
4. Prerequisites.
5. Local environment setup.
6. PostgreSQL startup.
7. Alembic migration commands.
8. API startup.
9. Docker Compose setup.
10. Google Vision credential mounting without committing secrets.
11. Optional Gemini key configuration.
12. Example `curl` commands:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/api/v1/jobs \
  -F 'file=@/absolute/path/register.pdf'

curl http://localhost:8000/api/v1/jobs/<job-id>

curl -OJ http://localhost:8000/api/v1/jobs/<job-id>/download
```

13. API docs URL.
14. Testing commands.
15. Storage layout.
16. Troubleshooting for missing credentials, missing OCR config, timeout, and absent output.
17. A clearly separated “Future production migration” section describing replacement of `JobExecutor` with Valkey/Celery workers and local storage with DigitalOcean Spaces. Do not implement those systems now.

## 24. Implementation order

Implement in this order, using tests first and committing after each working slice.

### Task 1: Project foundation and health endpoint

- Create packaging, settings, logging, request ID middleware, app factory, health route, and initial unit tests.
- Confirm `GET /health` passes without PostgreSQL.
- Suggested commit: `feat: scaffold FastAPI service and health endpoint`

### Task 2: Database model, repository, and migration

- Add SQLAlchemy setup, the job model, repository functions, Alembic configuration, and migration tests.
- Suggested commit: `feat: add persistent OCR job model`

### Task 3: Local storage and upload validation

- Add safe paths, chunked writes, limits, file-signature validation, atomic rename, cleanup, and tests.
- Suggested commit: `feat: add validated local upload storage`

### Task 4: Job API without real execution

- Add schemas, standard errors, create/list/get/download routes, and an injected no-op executor for tests.
- Suggested commit: `feat: add OCR job REST API`

### Task 5: Subprocess OCR runner

- Add command construction, log redirection, timeout handling, process termination, output validation, and fake CLI tests.
- Suggested commit: `feat: execute OCR through isolated CLI subprocess`

### Task 6: Background executor and recovery

- Add the single-worker executor, complete job lifecycle, startup recovery, and lifecycle integration tests.
- Suggested commit: `feat: process OCR jobs in bounded background executor`

### Task 7: Readiness, Docker, and developer workflow

- Add readiness checks, Dockerfile, Compose, Makefile, `.env.example`, and container tests.
- Suggested commit: `build: add reproducible local PostgreSQL stack`

### Task 8: CI and documentation

- Add GitHub Actions, complete README, run full verification, and fix all failures.
- Suggested commit: `docs: document backend setup and OCR integration`

## 25. Acceptance criteria

The implementation is accepted only when all of the following are true:

- The repository is standalone and contains no Angular code or copied OCR source.
- The upstream OCR version is pinned to an exact commit SHA.
- `GET /health` works without database access.
- `GET /ready` reports readiness accurately.
- A valid upload returns `202` and persists a `PENDING` job.
- The request does not wait for OCR completion.
- The executor transitions the job through `PROCESSING` to `COMPLETED` or `FAILED`.
- OCR executes through a subprocess with `shell=False`.
- Uploaded filenames cannot control filesystem paths.
- Oversized, unsupported, empty, and signature-mismatched files are rejected safely.
- A completed XLSX can be downloaded.
- An incomplete job cannot be downloaded.
- API responses never reveal absolute filesystem paths, credentials, stack traces, or unbounded stderr.
- Restart recovery handles stale `PROCESSING` jobs.
- Unit, API, fake-CLI integration, and migration tests pass.
- Ruff format check, Ruff lint, and Mypy pass.
- Docker Compose configuration validates.
- README documents the Phase 1 limitation and future Celery/Valkey migration.

## 26. Explicit non-goals

Do not implement these in this milestone:

- Angular frontend
- User login or roles
- JWT or OAuth
- Multiple-file or batch upload
- PDF page splitting in the API
- Page-level queue jobs
- Human review APIs
- Record correction APIs
- CSV export generation in the API
- Valkey, Redis, Celery, RabbitMQ, or Kafka
- DigitalOcean Spaces or S3 storage
- Multiple API workers
- Multiple concurrent OCR jobs
- WebSockets or Server-Sent Events
- Job cancellation or retry endpoints
- Automatic cleanup or retention policies
- Parsing record counts from Rich terminal output
- Importing the upstream OCR pipeline directly

## 27. Final verification and report

Before finishing:

1. Run all commands in the Quality commands section that are supported by the environment.
2. Inspect `git diff --check`.
3. Search for committed secrets and generated storage artifacts.
4. Confirm Docker and `.env.example` use an exact upstream commit SHA.
5. Confirm every API response schema excludes internal paths.
6. Confirm tests do not contact Google Vision or Gemini.
7. Confirm Uvicorn is configured with one worker.
8. Confirm no Valkey or Celery dependency was added.

Return a concise final report containing:

- Implemented architecture.
- Files created.
- Database migration name.
- Endpoint list.
- Exact upstream OCR commit SHA used.
- Commands run and actual pass/fail results.
- Any environmental verification that could not be performed, stated honestly.
- The next recommended milestone, limited to adding authentication or replacing the executor with a durable queue; do not implement either automatically.
