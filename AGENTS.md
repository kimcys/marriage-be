# AGENTS.md — marriage-be

Backend for the Marriage OCR system: a FastAPI service (Python package `marriage_ocr_api`, `src/` layout) plus Celery
workers. It owns the HTTP API, auth, batches/documents/jobs/records, exports, storage and scheduling. It does **not**
do OCR itself. OCR lives in the separate `marriage-ocr` repository (https://github.com/kimcys/marriage-ocr), which this
repo installs at a pinned git SHA and runs as a subprocess (`python -m marriage_ocr.cli process|process-typed|classify
...`). The web UI is `../marriage-fe`. The workspace-level overview is `../AGENTS.md`.

The data is real people's Islamic marriage registry records (Nikah = marriage, Cerai = divorce, Rujuk = reconciliation).
Treat all of it as sensitive personal data.

---

## 1. Quick start

```bash
python -m pip install -e '.[dev]'          # Python >= 3.12
cp .env.example .env                        # then adjust; .env is gitignored
docker compose up -d postgres               # PostgreSQL 16 on localhost:5432
alembic upgrade head                        # apply migrations
uvicorn marriage_ocr_api.main:app --reload  # http://localhost:8000/docs
```

`make` targets (see `Makefile`): `install`, `format` (check only), `lint`, `typecheck`, `test`, `migrate`, `run`,
`docker-up`, `docker-down`, `verify`.

Full local stack with Celery (api + worker + beat + postgres + valkey, optional MinIO profile for S3 testing):

```bash
docker compose up -d --build postgres api
docker compose --profile s3 up -d          # adds MinIO for STORAGE_BACKEND=s3 testing
```

Create the first user (there is no self-registration; the product is an internal tool). Inside the running container:

```bash
docker compose exec api python -m marriage_ocr_api.auth.cli create-user --email you@example.com --role ADMIN
# also: list-users, assign-batches (set "added by" on batches that predate the column)
```

Roles are `ADMIN` and `REVIEWER`. Passwords are bcrypt-hashed; tokens are JWTs (HS256, default 7-day expiry, no refresh).

## 2. Verification — run before every push

```bash
ruff format --check . && ruff check . && mypy src && pytest -q -m "not integration"   # == `make verify` + marker
python scripts/export_openapi.py        # regenerates artifacts/openapi.json; CI runs it, rerun if schemas change
alembic upgrade head && alembic downgrade base   # migrations must round-trip
pytest -q -m integration -k 'not docker_only_end_to_end_smoke'      # needs Postgres
pytest -q -m integration tests/integration/test_docker_e2e.py       # needs Docker
```

**Run the lint/type checks even when you only touched `scripts/`.** A lint failure on `main` blocks the deploy
(see section 7). Tests never need real Google credentials, Gemini keys or network; they use
`tests/fixtures/fake_ocr_cli.py` (set `OCR_MODULE=tests.fixtures.fake_ocr_cli`) and `JOB_EXECUTOR_BACKEND=thread_pool`.

## 3. Repository layout

```
src/marriage_ocr_api/
  main.py            create_app(), lifespan (executors, startup recovery), error handlers, router wiring, CORS
  core/              config.py (pydantic Settings), logging.py, request_id.py (X-Request-ID middleware)
  api/               dependencies.py (DB session / settings deps), errors.py (ApiError envelope),
                     routers/ (health, jobs; __init__ re-exports every router)
  auth/              JWT + bcrypt, require_user/require_admin deps, routers (login), user_admin.py (/users),
                     cli.py (create-user etc.), models.py, status.py (Role)
  batches/           Batch + Document models, status enums (BatchStatus, DocumentStatus, ExportStatus, DocumentType),
                     routers, service, pdf_splitter.py (one job per handwritten PDF page), document_ingest.py
  onedrive/          OneDrive share-link ingestion: routers, service, runner.py (subprocess to marriage-ocr),
                     classification.py (maps classify output to DocumentType), local_intake.py, celery_executor.py, tasks.py
  jobs/              OCR job lifecycle: runner.py (builds + runs the subprocess), processing.py (materialize input,
                     run, import records, push output), service.py (retry, startup/stale recovery), celery_app.py
                     (queues + beat schedule), tasks.py, gemini_batch.py (Gemini Batch Mode), preflight.py, paths.py
  records/           OCR records + review: models, repositories, service (corrections/revisions), importer.py
                     (CSV/XLSX -> rows), not_recorded.py (auto-approve "TIADA MAKLUMAT"), routers
  exports/           XLSX/CSV export creation, writer.py, tasks.py (daily stale-export cleanup), routers
  activity/          audit/activity log (who did what), admin-only listing
  storage/           StorageService abstraction: local.py, s3.py (MinIO/DO Spaces), factory.py, local_copies.py
  maintenance/       clean_local_copies.py (frees worker disk; see Gotchas)
  db/                SQLAlchemy base, session factory, models.py (OCRJob), repositories.py
migrations/versions/ Alembic 0001..0016 (zero-padded sequential ids; test_migration_ids.py enforces the scheme)
scripts/             one-off ops scripts (retry/reprocess/backfill/approve); NOT copied into the image
tests/unit, tests/integration, tests/fixtures
docs/frontend-integration.md   API contract notes for the frontend
docs/superpowers/              historical design specs and plans (stage 6-8); background reading only
artifacts/openapi.json         generated OpenAPI schema (via scripts/export_openapi.py)
```

Module convention inside a domain package: `models.py` (SQLAlchemy), `repositories.py` (queries), `schemas.py`
(request bodies), `response_models.py` (responses), `service.py` (business logic), `routers.py`, `status.py` (enums).
Follow it for new domains. Routers declare their own `/api/v1/...` prefix; every operation sets an explicit
`operation_id` (OpenAPI is pinned for frontend codegen, so don't rename existing ones).

## 4. Domain model and flow

1. A user creates a **Batch** (`POST /api/v1/batches`, with name plus optional daerah/negeri location).
2. Documents arrive **only** through OneDrive share links: `POST /api/v1/batches/{id}/onedrive-links`. There is no
   direct upload endpoint. A repeat POST of the same URL returns the existing submission unchanged.
3. A Celery task (`onedrive.fetch_submission`) downloads the link via marriage-ocr (`onedrive fetch-public`, with a
   browser fallback), **classifies** every file (`classify` / `classify-stack`; up to 3 single images per Vision call,
   `ONEDRIVE_CLASSIFY_STACK_SIZE`), and creates a Document + OCR Job per routable file. Mixed types in one link are
   expected. Unroutable files are recorded as skipped and can be re-classified manually
   (`.../skipped-files/classify`, `.../skipped-files/reclassify`).
4. Each **OCR Job** (`run_ocr_job` Celery task, queue `ocr_jobs`) runs the matching marriage-ocr command with the config
   file picked by `DocumentType` (`jobs/runner.py::_CONFIG_FILENAME_BY_DOCUMENT_TYPE`), writes output under
   `storage/jobs/<job-id>/{input,output,debug,logs}`, then `records/importer.py` imports the XLSX (handwritten) or CSV
   (typed) into **Record** rows.
5. Handwritten multi-page PDFs are split one job per page; typed forms are never split.
6. Records start `PENDING_REVIEW` or `APPROVED` (auto-approved when only "not recorded" issues exist and
   confidence is high; threshold `LOW_CONFIDENCE_THRESHOLD = 0.70`). Reviewers PATCH/approve/reject records;
   revisions are kept. Records carry a record type (NIKAH/CERAI/RUJUK) and free-form field dicts.
7. **Exports** (CSV/XLSX) are created after review and downloaded via presigned URL (S3) or file response (local).

Enums to know (all `StrEnum` in `status.py` modules): `JobStatus` (PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED),
`BatchStatus` (DRAFT, QUEUED, PROCESSING, REVIEW_REQUIRED, COMPLETED, FAILED, CANCELLED), `DocumentStatus`,
`ExportStatus`, `RecordStatus` (PENDING_REVIEW, APPROVED), `OneDriveSubmissionStatus` (PENDING, FETCHING, FETCHED,
FAILED), `Role`, and `DocumentType` — 12 values: `HANDWRITTEN_REGISTER` (= Nikah), `HANDWRITTEN_{CERAI,RUJUK}_{LEGACY,MODERN}`,
`TYPED_NIKAH_{LEGACY,MODERN}`, `TYPED_{CERAI,RUJUK}_{LEGACY,MODERN}`, and the legacy holdover `TYPED_BORANG_4B`.
`HANDWRITTEN_REGISTER` and `TYPED_BORANG_4B` are kept for stored rows and API compatibility; never rename or remove
enum values, and never emit `TYPED_BORANG_4B` from new classification.

Batch/document status is derived: use `recompute_document_status` / `recompute_batch_status`
(`batches/repositories.py`) after changing job state instead of setting statuses by hand.

## 5. API surface (all under `/api/v1`, Bearer JWT unless noted)

- `GET /health`, `GET /ready` (no auth; `/ready` does not check Valkey)
- Auth: `POST /auth/login` → `{access_token, token_type, role}`; users admin: `GET/POST /users`, `PATCH /users/{id}`,
  `POST /users/{id}/password` (admin only)
- Batches: `POST/GET /batches`, `GET /batches/stats`, `GET/PATCH/DELETE /batches/{id}` (delete = admin),
  `POST /batches/{id}/cancel`, `GET /batches/{id}/documents/{doc}/download`
- OneDrive: `POST/GET /batches/{id}/onedrive-links`, `POST .../{submission}/retry`, skipped-files classify/reclassify,
  `DELETE .../{submission}`
- Jobs: `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/retry`, `GET /jobs/{id}/download`
- Records: `GET /records`, `GET /records/{id}`, `PATCH /records/{id}`, `DELETE /records/{id}`, approve/reject,
  `GET /records/{id}/revisions`, `POST /records/bulk-approve`; sort modes `newest` and `worst_first`
- Exports: `POST/GET /exports`, `GET /exports/{id}`, `GET /exports/{id}/download`, `DELETE /exports/{id}` (admin)
- Activity: `GET /activity` (admin)

Errors always use the `ApiError` envelope `{error: {code, message, request_id, details?}}` with the `X-Request-ID`
header echoed. Raise `ApiError(status, "CODE", "message")`; don't return ad-hoc error bodies and don't leak stack traces.
When request/response shapes change: update `docs/frontend-integration.md`, regenerate `artifacts/openapi.json`, and
update `../marriage-fe/src/app/services/api.service.ts`.

## 6. Configuration

All settings are in `core/config.py` (`Settings`, env-driven, `.env` supported). Key variables:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...` (Managed Postgres in prod) |
| `JOB_EXECUTOR_BACKEND` | `celery` (required in prod; compose default) or `thread_pool` (local/tests, no persistence) |
| `VALKEY_URL` | Celery broker/result backend (`redis://` or `rediss://`) |
| `STORAGE_BACKEND` | `local` or `s3`; prod uses `s3` (DO Spaces) via the `MINIO_*` variable names |
| `JWT_SECRET_KEY` | the code default is insecure; prod compose refuses to start without a real one |
| `OCR_*` | executable, module, config paths/dir, timeout (3600s), `OCR_MAX_CONCURRENT_JOBS` (worker concurrency) |
| `ONEDRIVE_FETCH_TIMEOUT_SECONDS` | 21600; a whole link fetch+classify+ingest is far longer than one OCR job |
| `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS` | real OCR only; creds file must be readable by uid 10001 in the container |
| `GEMINI_BATCH_ENABLED` / `_MAX_JOBS` / `_FALLBACK_HOURS` | Gemini Batch Mode (half price, hours of latency); for bulk backlogs only |
| `TYPED_READER` | `vision` (default) or `gemini`; gemini falls back to vision per PDF on error |
| `AUTO_APPROVE_NOT_RECORDED` | auto-approve records whose only issues are "TIADA MAKLUMAT" |
| `CORS_ORIGINS` | comma-separated list |
| `MARRIAGE_OCR_GIT_URL` / `MARRIAGE_OCR_GIT_REF` | build-time pin of the OCR repo (see Gotchas) |

Adding a setting: add it to `Settings` (with a validator if constrained), `.env.example`, `.env.production.example`,
and `docker-compose*.yml` environment blocks, and mention it here if operators need to know.

## 7. Celery, deployment and operations

- `jobs/celery_app.py`: default queue `ocr_jobs`; housekeeping tasks use `ocr_maintenance`; workers listen to both
  (`WORKER_QUEUES`). Settings: `acks_late`, `reject_on_worker_lost`, `prefetch_multiplier=1`, task time limits derived
  from `OCR_TIMEOUT_SECONDS`.
- Beat schedule (singleton — run on exactly one host): `recover-stale-ocr-jobs` (5 min), `gemini-batch-tick` (5 min),
  `recover-stale-onedrive-submissions` (5 min), `cleanup-stale-exports` (daily).
- Under Celery the API does **not** fail in-flight jobs at startup (workers may still be running them); only the
  `thread_pool` backend does that. Truly abandoned jobs are caught by the stale-job beat task.
- `docker-compose.yml` = single-host dev stack. `docker-compose.production.yml` = `api`, `worker`, `beat` only, pointed at
  Managed Postgres, a Valkey/Redis instance and a Spaces bucket; every node must share the same bucket
  (`jobs/processing.py` materializes inputs from Spaces and pushes outputs back).
- **CI/CD** (`.github/workflows/ci.yml`): jobs `unit` (format, lint, mypy, alembic up, OpenAPI export, pytest),
  `integration`, `docker-e2e`, then `deploy` on pushes to `main` only. Deploy builds and pushes the image to the
  DigitalOcean registry tagged by commit SHA, finds droplets by tag (`marriage-be-api`, `marriage-be-worker`), SSHes in,
  `git pull`s, prunes images, pulls and recreates `api beat` / `worker`, then health-checks `/health` on the API host.
  Adding a worker = create a droplet with the tag and its `.env.production`; the next deploy picks it up.
- Production shape (as last recorded): one API droplet running api + beat + caddy + the Valkey container, and **four**
  worker droplets, in DigitalOcean sgp1. API is at https://api.lauktech.xyz. Scale throughput by adding worker droplets,
  not by raising concurrency on the API box (it OOM'd once with 4 concurrent jobs on 4 GB).

## 8. Gotchas and hard-won rules

- **OCR SHA pin.** After any `marriage-ocr` change: commit and push it first, then replace the old SHA everywhere
  (`grep -rn <old-sha> .`): `Dockerfile`, `README.md`, `.env.example`, `.env.production.example`, `docker-compose.yml`,
  `docker-compose.production.yml`, `.github/workflows/ci.yml`, `src/marriage_ocr_api/core/config.py`. A missed spot makes
  CI, local and prod run different OCR code.
- **Deploys recreate workers** with a 5-minute stop grace period. Never recreate/restart containers by hand or start a
  prod script while a deploy is running; wait until the Deploy job finishes (a manual `up -d worker` mid-deploy once
  left a worker down; a script started mid-deploy gets killed, exit 137).
- **Always name services** in `docker compose up -d` (`api beat` on the API host, `worker` on workers) and pass
  `--env-file .env.production` to every compose subcommand. Bare `up -d` starts every service on that host.
- **Running one-off prod scripts.** Scripts in `scripts/` are piped into the worker container by the user:
  `ssh -i ~/.ssh/marriage-be-deploy root@<worker> 'cd /root/marriage-be && docker compose --env-file .env.production
  exec -T worker python - --execute' < /absolute/path/script.py`. Rules for new scripts: dry-run by default and require
  `--execute` to write; accept `--limit`/`--batch-id` style guards; import `marriage_ocr_api.db.models` (and other model
  modules) so every table is registered before commit; use the `contextlib.suppress(NameError)` `sys.path` idiom from
  existing scripts since `__file__` is absent when piped; do not rely on files outside the image. Claude Code is blocked
  from SSH/doctl writes on prod, so hand the exact command to the user to run with `!`.
- **Queue contention.** `onedrive.fetch_submission` shares worker slots with `run_ocr_job` (queue `ocr_jobs`). One
  fetch can take 30+ minutes and enqueue hundreds of jobs, so re-runs can sit PENDING for an hour. Check queue depth
  (`valkey-cli llen ocr_jobs` on the Valkey host) and worker slot usage before calling anything stuck.
- **Worker disks fill up.** Each release image is ~6 GB and workers keep local copies. A 100%-full worker fails every job
  instantly with `INTERNAL_PROCESSING_ERROR` and no worker log line. Check `df` on **all** workers, not just one. Reclaim
  space with `python -m marriage_ocr_api.maintenance.clean_local_copies` (dry run first; `--apply` to delete; only deletes
  local copies confirmed present in Spaces). If even `docker exec` fails, free ~100 MB first (`apt-get clean`,
  `journalctl --vacuum-size=20M`).
- **Spaces is the source of truth for originals.** Never delete objects there; only `gemini-batch/<job>/prepared` helper
  copies are ever removed. Don't delete the old `marriage-be_worker_storage` volume on the API host either.
- **Credentials file** (`google-vision.json`) must be `chown 10001:10001` and `chmod 400` on each droplet, else every
  classify/OCR run exits 1.
- **SSH**: ufw `limit 22` refuses more than 6 connections per 30s; use SSH `ControlMaster` for repeated calls.
- **Backfill/bulk flags.** `GEMINI_BATCH_ENABLED=true` and `TYPED_READER=gemini` were turned on for a backlog; they are
  meant to be reverted afterward. Don't change them casually while a re-run is pending.
- `onedrive_submissions.title` (migration 0016) stores the share folder name; `scripts/backfill_onedrive_titles.py`
  fills missing ones.
- Migrations: add a new numbered file under `migrations/versions/` (next id after 0016, same zero-padded naming);
  never edit applied migrations; provide a working `downgrade`; the integration tests exercise upgrade/downgrade.

## 9. Code style

- Ruff: line length 120, target py312, rule sets E, F, I, B, UP, N, SIM (B008 ignored for FastAPI `Depends`).
  `ruff format` is enforced in CI. Every module starts with `from __future__ import annotations`.
- mypy: `disallow_untyped_defs`, `check_untyped_defs`, `no_implicit_optional`, `warn_return_any`; missing stubs are
  ignored only for filetype/openpyxl/boto3/botocore/celery. Annotate everything; avoid `Any` and `# type: ignore`
  unless there is a comment explaining why.
- SQLAlchemy 2.0 typed style (`Mapped[...]`, `mapped_column`), sync sessions via `get_session_factory`. Timestamps are
  timezone-aware UTC (`datetime.now(UTC)`).
- Keep routers thin: validation and HTTP concerns in routers, logic in `service.py`, SQL in `repositories.py`.
- Comments in this codebase explain *why* (incidents, trade-offs) in full sentences; match that density and tone, and
  don't add comments that restate the code.
- Never log secrets, JWTs, share-link tokens or record contents. Use the module logger (`logging.getLogger(__name__)`).

## 10. Testing guidance

- Unit tests (`tests/unit`) use fakes and an in-memory/test DB setup from `tests/conftest.py`; integration tests are
  marked `@pytest.mark.integration` and need Postgres (and Docker for the e2e one).
- New endpoint: add route tests, a repository/service test, and update the OpenAPI contract test if shapes changed
  (`test_openapi_contract.py`, `test_openapi_export.py`).
- New migration: `test_migration_ids.py` and the integration migration tests must still pass.
- New script: add a focused unit test like `test_reprocess_typed_jobs.py` / `test_backfill_mas_kahwin.py`.
- Test fixtures must not contain real personal records; use obviously fake names and ICs.

## 11. Security and privacy

- Never commit `.env`, `.env.production`, `*.json` service-account keys (e.g. `marrige-ocr-*.json`, `ardent-time-*.json`
  are gitignored), `storage/`, or logs. Do not paste real record data into code, tests, commits or PR text.
- Auth: every router except health uses `require_user`; destructive or administrative routes use `require_admin`.
  Keep new routes protected by default.
- File downloads go through authenticated endpoints or presigned URLs; don't expose storage paths.
- There is a stray prompt file (`marriage-ocr-api-codex-prompt.md`) kept from early scaffolding; ruff excludes it.
  Treat it as historical.
