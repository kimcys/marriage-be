# Frontend Integration

## Auth

Use the existing session-based login flow from the frontend. Keep the `X-Request-ID` header on all API calls.

## Contract

The frontend should treat the API as a stable HTTP contract:

- Batch upload creates a batch first, then uploads a source document into that batch.
- Job progress is polled until the OCR run is complete.
- Review actions happen on the record endpoints, not inside the job endpoint.
- Export creation is a separate step after review approval.
- `operationId` values and request examples are pinned in OpenAPI for frontend code generation.

## Upload

- Create a batch with `POST /api/v1/batches`.
- Upload a document with `POST /api/v1/batches/{batch_id}/documents`.
- Poll the job with `GET /api/v1/jobs/{job_id}` and the job record list with `GET /api/v1/jobs/{job_id}/records` until processing completes.

## Review

- List records with `GET /api/v1/records`.
- Fetch a record with `GET /api/v1/records/{record_id}`.
- Patch a record with `PATCH /api/v1/records/{record_id}`.
- Approve a record with `POST /api/v1/records/{record_id}/approve`.
- Reject a record with `POST /api/v1/records/{record_id}/reject`.
- Inspect revisions with `GET /api/v1/records/{record_id}/revisions`.
- Bulk-approve records with `POST /api/v1/records/bulk-approve`.

## Exports

- Create an export with `POST /api/v1/exports`.
- Poll export status with `GET /api/v1/exports/{export_id}`.
- Download the generated artifact with `GET /api/v1/exports/{export_id}/download`.

## OpenAPI

Generate a fresh schema with:

```bash
python scripts/export_openapi.py
```

The output is written to `artifacts/openapi.json`.

Regenerate this file whenever request or response shapes change so the frontend contract and docs stay aligned.

## End-to-End Check

Run the Docker smoke test when you want to exercise the full stack:

```bash
pytest -q -m integration tests/integration/test_docker_e2e.py
```

The smoke test brings up the Compose stack, uploads a sample PDF, waits for OCR completion, approves the resulting records, and downloads an export.
