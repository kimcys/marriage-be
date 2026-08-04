# Stage 6 Design: OCR Records and Review

## Goal

Add the record and review surface for completed OCR jobs without changing the current job pipeline boundary. Stage 6 turns imported OCR output into editable backend records, tracks corrections as revisions, and exposes review endpoints for listing, detail, approval, rejection, and bulk approval.

## Current State

The backend already owns:

- job creation, upload validation, and subprocess execution;
- persistent `ocr_jobs` storage;
- job lifecycle APIs and download behavior;
- shared API error handling and request IDs.

The backend does not yet own:

- record materialization from OCR output;
- record-level review state;
- revision history;
- approval and rejection flows;
- bulk review operations.

## Design Summary

Use a dedicated record/review layer built on top of the existing PostgreSQL session and repository pattern. Keep job lifecycle and record lifecycle separate:

- jobs represent processing of an uploaded file;
- records represent extracted rows that can be corrected and approved by a reviewer;
- revisions represent immutable history of changes to a record.

The job worker imports OCR results into records once a job completes. Review endpoints operate only on backend record state and never mutate raw OCR artifacts.

## Data Model

### `ocr_records`

Store one row per extracted OCR record. Fields should support:

- stable UUID primary key;
- foreign key to the source job;
- source document or row identity from OCR output;
- current review status;
- extracted field values used by the frontend review UI;
- reviewer metadata;
- timestamps;
- a concurrency token such as `updated_at` or explicit integer version.

Recommended review statuses:

- `PENDING_REVIEW`
- `APPROVED`
- `REJECTED`

### `record_revisions`

Store an immutable history row whenever a reviewer changes a record. Each revision should capture:

- revision UUID;
- record UUID;
- actor identity if available;
- changed field payload;
- previous state snapshot or patch;
- reason or note when present;
- timestamp.

This is enough for a frontend to show review history and for exports to reconstruct the reviewed state later.

### Job import linkage

Add import bookkeeping so record creation from OCR output is idempotent. The import path should be able to re-run safely for the same job without duplicating records.

## API Surface

### Record browsing

- `GET /api/v1/records`
- `GET /api/v1/records/{record_id}`
- `GET /api/v1/jobs/{job_id}/records`

These endpoints should support pagination and optional review-status filtering. The job-scoped route is for browsing records from a single OCR run.

### Review actions

- `PATCH /api/v1/records/{record_id}` for corrections
- `POST /api/v1/records/{record_id}/approve`
- `POST /api/v1/records/{record_id}/reject`
- `POST /api/v1/records/bulk-approve`

The patch endpoint should update editable fields and create a revision in the same transaction.
Approve/reject endpoints should be idempotent for already-processed records when the requested state matches the current state.
Bulk approve should take an explicit list of record IDs and reject partial ambiguous selectors.

### Revision history

- `GET /api/v1/records/{record_id}/revisions`

Return revisions newest-first with enough detail for the frontend to explain what changed and when.

### Import hook

The record import itself should stay mostly internal. If an HTTP endpoint is needed for testability or operator tooling, keep it clearly internal or guarded. Prefer a service-layer import function called by the job completion path rather than a public API route.

## Service Boundaries

### `RecordRepository`

Own SQLAlchemy persistence for:

- creating records;
- fetching records and revisions;
- updating review status;
- recording revisions;
- bulk status changes;
- idempotent import checks.

### `RecordService`

Own orchestration and business rules for:

- importing OCR job output into records;
- validating editable fields;
- applying corrections;
- enforcing approval/rejection state transitions;
- handling concurrency conflicts;
- assembling API response models.

### API routers

Keep routers thin. They should:

- parse request payloads;
- call the service layer;
- translate errors into `ApiError`;
- return Pydantic response schemas.

## Concurrency and Idempotency

Use a concurrency check on update paths so stale edits fail cleanly. A record should not accept a correction if the caller’s observed version is stale.

Recommended behavior:

- `PATCH` and review endpoints require the latest row version or `updated_at`;
- concurrent edits return a deterministic conflict error;
- import checks avoid duplicate record creation for the same job and source identity;
- bulk approval should either process each requested record atomically in a transaction or fail the whole request if any record is stale or invalid.

## Error Handling

Use the existing API error envelope for:

- not found;
- invalid state transition;
- conflict;
- validation failure;
- import failure.

Recommended error codes:

- `RECORD_NOT_FOUND`
- `RECORD_CONFLICT`
- `RECORD_INVALID_STATE`
- `RECORD_IMPORT_FAILED`
- `INVALID_REVIEW_PAYLOAD`

## Testing Strategy

### Unit tests

Add tests for:

- repository create/read/update revision behavior;
- idempotent import logic;
- status transition rules;
- conflict detection;
- correction payload validation;
- bulk approve validation.

### API integration tests

Add tests for:

- listing records;
- reading a single record;
- patching corrections;
- viewing revision history;
- approve/reject behavior;
- bulk approve behavior;
- conflict responses.

### Lifecycle integration

Extend the existing job lifecycle coverage so that a completed OCR job can produce importable record data and the backend can expose the resulting records through the API.

## Implementation Notes

- Reuse the existing SQLAlchemy sync session pattern.
- Keep current `ocr_jobs` behavior intact.
- Do not move OCR parsing or provider logic into the backend.
- Do not depend on Google Vision or Gemini for tests.
- Do not require the real OCR package in the default test suite.

## Out of Scope For Stage 6

- export generation;
- UI implementation;
- auth/role redesign;
- changes to the OCR CLI contract unless a small backward-compatible import contract is proven missing.

