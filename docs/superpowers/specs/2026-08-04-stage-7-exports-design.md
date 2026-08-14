# Stage 7 Design: Batch-Scoped Exports and Storage

## Goal

Add batch-scoped CSV and XLSX export generation, plus a storage abstraction that supports local filesystem downloads and S3-compatible signed URLs.

## Current State

The backend already has:

- a working job upload and OCR execution path;
- persistent OCR records and review actions;
- local filesystem-based upload/download behavior for OCR job artifacts;
- a repository/service pattern for job and record persistence.

The backend does not yet have:

- batch or document entities;
- export entities or export APIs;
- a storage abstraction;
- S3-compatible export downloads;
- OpenAPI export tooling or frontend integration docs.

## Design Summary

Stage 7 adds a small batch/document layer so the export contract can stay batch-scoped without breaking the existing job flow.

- A `Batch` groups uploaded documents.
- A `Document` belongs to one batch and one OCR job.
- OCR jobs remain the processing unit for the worker.
- Export generation reads effective reviewed values from OCR records for all documents in a batch.
- Exports are generated asynchronously, stored through a storage service, and exposed through download endpoints.

The existing `/api/v1/jobs` upload path stays available for compatibility, but the batch-aware upload path becomes the primary route for new batch-scoped workflows.

## Data Model

### `batches`

Store batch metadata and status:

- `id`
- `name`
- `description`
- `status`
- `created_by`
- `created_at`
- `updated_at`
- `started_at`
- `completed_at`

Recommended statuses:

- `DRAFT`
- `QUEUED`
- `PROCESSING`
- `REVIEW_REQUIRED`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

### `documents`

Store one row per uploaded file:

- `id`
- `batch_id`
- `original_filename`
- `safe_filename`
- `media_type`
- `size_bytes`
- `sha256`
- `storage_key`
- `status`
- `page_count`
- timestamps

Recommended statuses:

- `UPLOADED`
- `QUEUED`
- `PROCESSING`
- `PROCESSED`
- `FAILED`
- `CANCELLED`

### `ocr_jobs`

Extend the existing job table so each job is tied to a batch and document. Keep the existing job lifecycle behavior intact, but add the parent keys needed for export aggregation.

Recommended new columns:

- `batch_id`
- `document_id`

### `ocr_records`

Extend the current record table with batch/document linkage and export-friendly fields:

- `batch_id`
- `document_id`
- `source_page`
- `source_record_index`
- `normalized_data`
- `corrected_data`
- `review_status`

Keep the current review API behavior by treating the effective record as the merge of normalized data and corrections, with corrections winning.

### `exports`

Store one row per export request:

- `id`
- `batch_id`
- `format`
- `status`
- `storage_key`
- `record_count`
- `created_by`
- `error_message`
- `created_at`
- `completed_at`

Recommended statuses:

- `PENDING`
- `PROCESSING`
- `COMPLETED`
- `FAILED`

## API Surface

### Batch and document workflow

- `POST /api/v1/batches`
- `GET /api/v1/batches`
- `GET /api/v1/batches/{batch_id}`
- `POST /api/v1/batches/{batch_id}/documents`

The document upload route should reuse the existing file validation and safe storage logic.

The existing `POST /api/v1/jobs` route remains available as a compatibility path and can create a single-document batch internally when the caller does not use batch routes directly.

### Exports

- `POST /api/v1/exports`
- `GET /api/v1/exports`
- `GET /api/v1/exports/{export_id}`
- `GET /api/v1/exports/{export_id}/download`

Export creation request:

```json
{
  "batch_id": "uuid",
  "format": "XLSX",
  "include_unreviewed": false
}
```

Use page-based pagination for export listings:

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "pages": 0
}
```

## Storage Abstraction

Introduce a storage protocol so export generation and download can target either the local filesystem or an S3-compatible backend.

```python
class StorageService(Protocol):
    def put_file(self, source: Path, key: str) -> StoredObject: ...
    def open_read(self, key: str) -> BinaryIO: ...
    def materialize(self, key: str, destination: Path) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def signed_download_url(self, key: str, expires_seconds: int) -> str | None: ...
```

### Local storage

Keep the local root under `./var/storage` and use the prompt’s key layout:

- `batches/{batch_id}/documents/{document_id}/input/{safe_filename}`
- `jobs/{job_id}/logs/stdout.log`
- `jobs/{job_id}/logs/stderr.log`
- `jobs/{job_id}/result/result.json`
- `jobs/{job_id}/result/result.xlsx`
- `exports/{export_id}/records.csv`
- `exports/{export_id}/records.xlsx`

### S3-compatible storage

Add an optional MinIO profile for testing and keep the adapter compatible with DigitalOcean Spaces by configuring endpoint, bucket, region, key, and secret.

The adapter should not assume AWS-specific hostnames.

## Export Generation

Generate exports asynchronously using a bounded background executor, following the existing in-process job execution pattern.

Export writer rules:

- CSV must be UTF-8 with BOM so Excel opens it cleanly.
- XLSX must be written with OpenPyXL.
- Column order must be stable and derived from the OCR contract data, not arbitrary dictionary order.
- Export rows must use effective reviewed values, with corrections applied over normalized data.
- `include_unreviewed=false` must omit records that have not been approved or otherwise made exportable.
- `include_unreviewed=true` must include the current effective values for review-pending records too.

Export status transitions should be transactional and idempotent:

- `PENDING` when created
- `PROCESSING` when the worker starts
- `COMPLETED` when the file is stored successfully
- `FAILED` with an error message if generation or storage fails

## Download Behavior

Use the storage backend to decide download behavior:

- Local storage: return a normal authenticated file download with `Content-Disposition`.
- S3-compatible storage: return a short-lived signed download URL from the API, or redirect to it if the implementation keeps the route browser-friendly.

The response shape must remain deterministic so the frontend can use it without special-casing by format.

## Testing Strategy

### Unit tests

Add tests for:

- batch and export repository persistence;
- export row assembly from reviewed record data;
- CSV generation with BOM;
- XLSX workbook contents and stable column order;
- local storage materialization and deletion;
- S3 storage URL generation and key mapping;
- status transitions and failure handling.

### Integration tests

Add tests for:

- creating a batch and uploading a document;
- creating an export from a batch;
- polling export status until completion;
- downloading the exported CSV/XLSX artifact;
- optional MinIO-backed S3 adapter behavior when the profile is enabled.

### Regression tests

Keep the current job, record review, and fake OCR lifecycle tests passing while the export layer is introduced.

## Documentation Deliverables

- `scripts/export_openapi.py` should write `artifacts/openapi.json`
- `docs/frontend-integration.md` should document login, refresh, upload, polling, review, and download flows
- `README.md` should document the local export workflow and the optional S3 profile

## Files Expected To Change

- Create: `src/marriage_ocr_api/batches/__init__.py`
- Create: `src/marriage_ocr_api/batches/models.py`
- Create: `src/marriage_ocr_api/batches/repositories.py`
- Create: `src/marriage_ocr_api/batches/service.py`
- Create: `src/marriage_ocr_api/batches/routers.py`
- Create: `src/marriage_ocr_api/exports/__init__.py`
- Create: `src/marriage_ocr_api/exports/models.py`
- Create: `src/marriage_ocr_api/exports/repositories.py`
- Create: `src/marriage_ocr_api/exports/service.py`
- Create: `src/marriage_ocr_api/exports/routers.py`
- Create: `src/marriage_ocr_api/exports/writer.py`
- Create: `src/marriage_ocr_api/storage/base.py`
- Create: `src/marriage_ocr_api/storage/s3.py`
- Modify: `src/marriage_ocr_api/storage/local.py`
- Modify: `src/marriage_ocr_api/core/config.py`
- Modify: `src/marriage_ocr_api/db/base.py`
- Modify: `src/marriage_ocr_api/db/models.py`
- Modify: `src/marriage_ocr_api/db/repositories.py`
- Modify: `src/marriage_ocr_api/jobs/service.py`
- Modify: `src/marriage_ocr_api/jobs/executor.py`
- Modify: `src/marriage_ocr_api/api/routers/__init__.py`
- Modify: `src/marriage_ocr_api/main.py`
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Create: `scripts/export_openapi.py`
- Create: `docs/frontend-integration.md`
- Create: `tests/unit/test_batch_repository.py`
- Create: `tests/unit/test_export_repository.py`
- Create: `tests/unit/test_export_writer.py`
- Create: `tests/unit/test_storage_s3.py`
- Create: `tests/integration/test_batch_upload_and_export.py`
- Create: `tests/integration/test_export_download.py`
- Create: `tests/integration/test_minio_storage.py`

## Out Of Scope

- WebSockets or server-sent events
- frontend code generation committed into this repo
- redesigning the OCR engine itself
- changing the current record review API shape beyond what exports need

