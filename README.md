# Marriage OCR API

FastAPI backend for the upstream [`marriage-ocr`](https://github.com/kimcys/marriage-ocr) project.

## Architecture

This repository only hosts the API, job storage, and database layer.
OCR processing stays in the separate `marriage-ocr` repository and is executed as a subprocess with:

```bash
python -m marriage_ocr.cli process --input ... --output ... --debug ... --config ... --reset-output
```

Documents are ingested via a OneDrive share link (`POST /api/v1/batches/{batch_id}/onedrive-links`):
a background task fetches the link, classifies every file it finds (mixed Nikah/Cerai/Rujuk,
handwritten/typed content in one link is expected, not an edge case), and routes each routable
file to its own job. A repeat POST of a URL already submitted returns that submission's existing
state unchanged rather than re-fetching.

## Job Execution

`JOB_EXECUTOR_BACKEND` selects how a submitted job actually runs:

- `celery` (the default in `docker-compose.yml` and required in production) dispatches jobs over
  Valkey (self-hosted) or Managed Redis (production) to one or more `worker` processes -- a
  durable queue that survives an API restart and scales horizontally across Droplets. See
  "Production Deployment" below.
- `thread_pool` (the default for local `pip install -e` runs and what the test suite uses) runs
  jobs in a single in-process thread with no persistence across restarts -- fine for local
  development, never for production. On startup, the app marks any stale `PROCESSING` job as
  `FAILED` with `PROCESS_INTERRUPTED` regardless of which backend is active, since a hard restart
  can still catch an in-flight job either way.

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

Documents are ingested exclusively via OneDrive share links -- there is no
direct file-upload endpoint. See [`docs/frontend-integration.md`](docs/frontend-integration.md)
for the full contract.

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/api/v1/batches \
  -H 'Content-Type: application/json' \
  -d '{"name": "Batch 1"}'

curl -X POST http://localhost:8000/api/v1/batches/<batch-id>/onedrive-links \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://1drv.ms/..."}'

curl http://localhost:8000/api/v1/jobs?batch_id=<batch-id>

curl http://localhost:8000/api/v1/records?batch_id=<batch-id>
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

Optional export storage can also use an S3-compatible backend. The local adapter remains the default, but the repository now includes an S3-compatible storage service and an optional MinIO profile in Docker Compose for testing that path.

## Testing

Fast unit and contract checks:

```bash
python scripts/export_openapi.py
ruff format --check .
ruff check .
mypy src
alembic upgrade head
alembic downgrade base
pytest -q -m "not integration"
```

Integration and Docker checks:

```bash
pytest -q -m integration -k 'not docker_only_end_to_end_smoke'
pytest -q -m integration tests/integration/test_docker_e2e.py
docker compose config
docker compose build api
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose run --rm api pytest -q -m "not integration"
docker compose down -v
```

The repository also includes mocked tests and health checks so CI and local development do not require real Google credentials.

The Docker build and Compose defaults pin the upstream OCR checkout to `c82d29dd662591e6bc0c26b40fc2ad2cf2b93420`, which matches the current `origin/main` of `marriage-ocr`.

Frontend contract notes live in [`docs/frontend-integration.md`](docs/frontend-integration.md).

## Troubleshooting

- Missing credentials: verify `GOOGLE_APPLICATION_CREDENTIALS` points to a readable JSON file.
- Missing OCR config: verify `/opt/marriage-ocr/config/handwritten.yaml` and `/opt/marriage-ocr/config/typed_borang4b.yaml` exist in the image, or that `OCR_CONFIG_PATH_HANDWRITTEN`/`OCR_CONFIG_PATH_TYPED`/`OCR_CONFIG_DIR` point at valid local overrides.
- Timeout: increase `OCR_TIMEOUT_SECONDS` if the OCR run legitimately takes longer.
- Absent output: check the job logs under `storage/jobs/<job-id>/logs/`.

## Production Deployment (Multiple DigitalOcean Droplets)

`docker-compose.yml` (single Droplet, local Postgres/Valkey/MinIO containers)
is for development. For real horizontal scaling -- multiple worker Droplets
processing OCR jobs off the same queue, independent of the API Droplet --
use `docker-compose.production.yml` with `.env.production.example` as a
starting point for your own `.env.production`.

That file defines only `api`, `worker`, and `beat` (no local
postgres/valkey/minio) and requires everything to point at shared,
externally-managed services instead:

- **Managed Database for PostgreSQL** -- `DATABASE_URL`.
- **Managed Redis** -- `VALKEY_URL` (fills Valkey's broker role; use `rediss://`).
- **Spaces** -- `SPACES_ENDPOINT_URL`/`SPACES_BUCKET_NAME`/`SPACES_REGION_NAME`/
  `SPACES_ACCESS_KEY_ID`/`SPACES_SECRET_ACCESS_KEY`, with `STORAGE_BACKEND=s3`.
  This is what actually makes multi-Droplet workers possible: `jobs/processing.py`
  materializes a job's input from Spaces if it isn't already on that
  worker's local disk, and pushes the completed output back up so any API
  instance can serve its download via a presigned URL -- not just the
  Droplet that happened to run the job.

Typical rollout:

```bash
# Build and tag once (CI, or manually), push to a registry:
docker build -t registry.digitalocean.com/your-registry/marriage-be:latest .
docker push registry.digitalocean.com/your-registry/marriage-be:latest

# On the API Droplet:
docker compose -f docker-compose.production.yml --env-file .env.production up -d api

# On each worker Droplet (add more Droplets, or `--scale worker=N` on one,
# to add throughput -- Celery load-balances across whatever's listening on
# the queue, no code changes needed):
docker compose -f docker-compose.production.yml --env-file .env.production up -d worker

# On exactly ONE Droplet, total -- beat is a singleton scheduler:
docker compose -f docker-compose.production.yml --env-file .env.production up -d beat
```
