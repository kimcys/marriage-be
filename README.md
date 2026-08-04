# Marriage OCR API

FastAPI backend for the upstream [`marriage-ocr`](https://github.com/kimcys/marriage-ocr) project.

## Architecture

This repository only hosts the API, job storage, and database layer.
OCR processing stays in the separate `marriage-ocr` repository and is executed as a subprocess with:

```bash
python -m marriage_ocr.cli process --input ... --output ... --debug ... --config ... --reset-output
```

The API accepts one uploaded file, stores it locally under a generated job directory, creates a job row in PostgreSQL, returns `202 Accepted`, and lets clients poll for completion.

## Phase 1 Limitation

Phase 1 uses a bounded in-process executor with one worker.
It is not a durable queue.
If the API restarts, queued or running OCR work can be interrupted.
On startup, the app marks stale `PROCESSING` jobs as `FAILED` with `PROCESS_INTERRUPTED`.

## Prerequisites

- Python 3.12
- PostgreSQL 16
- Docker and Docker Compose for the container workflow
- A Google Vision service-account JSON file for real OCR runs

## Local Setup

Install dependencies:

```bash
python -m pip install -e '.[dev]'
```

Set environment variables with `.env.example` as a guide.

## PostgreSQL

Start PostgreSQL locally through Docker Compose:

```bash
docker compose up -d postgres
```

Run migrations:

```bash
alembic upgrade head
```

Rollback migrations:

```bash
alembic downgrade base
```

## Run the API

```bash
uvicorn marriage_ocr_api.main:app --reload
```

OpenAPI docs:

- http://localhost:8000/docs
- http://localhost:8000/redoc
- http://localhost:8000/openapi.json

## Docker Compose

Build and start the stack:

```bash
docker compose up -d --build postgres api
```

The API service runs `alembic upgrade head` before launching Uvicorn.
The local job storage directory is mounted at `./storage`.

## Google Vision Credentials

Mount a service-account JSON file outside the repository and point `GOOGLE_APPLICATION_CREDENTIALS` at it.
The container expects the file at `/run/secrets/google-vision.json`.

Example:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/secrets/google-vision.json"
docker compose up -d api
```

## Gemini Key

`GEMINI_API_KEY` is optional.
Leave it empty unless the upstream OCR configuration requires it.

## API Examples

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/api/v1/jobs \
  -F 'file=@/absolute/path/register.pdf'

curl http://localhost:8000/api/v1/jobs/<job-id>

curl -OJ http://localhost:8000/api/v1/jobs/<job-id>/download
```

## Storage Layout

Each job gets its own directory:

```text
storage/jobs/<job-id>/
├── input/
│   └── source.<ext>
├── output/
│   └── result.xlsx
├── debug/
└── logs/
    ├── stdout.log
    └── stderr.log
```

## Testing

```bash
ruff format --check .
ruff check .
mypy src
pytest -q
alembic upgrade head
alembic downgrade base
docker compose config
docker compose build api
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose run --rm api pytest -q
docker compose down -v
```

The repository also includes mocked tests and health checks so CI and local development do not require real Google credentials.

The Docker build and Compose defaults pin the upstream OCR checkout to `ad8235c5186c100dea723f7d6a011150dfd18dad`, which matches the current `origin/main` of `marriage-ocr`.

## Troubleshooting

- Missing credentials: verify `GOOGLE_APPLICATION_CREDENTIALS` points to a readable JSON file.
- Missing OCR config: verify `/opt/marriage-ocr/config/production.yaml` exists in the image or the local override path is correct.
- Timeout: increase `OCR_TIMEOUT_SECONDS` if the OCR run legitimately takes longer.
- Absent output: check the job logs under `storage/jobs/<job-id>/logs/`.

## Future Production Migration

Phase 1 intentionally keeps execution simple.
For production, the natural next steps are:

1. Replace `JobExecutor` with a durable worker queue such as Valkey/Celery.
2. Move local storage to object storage such as DigitalOcean Spaces.
3. Keep the current HTTP and database contract so the frontend does not need to change.
