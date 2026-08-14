# Stage 8 Frontend Contract Design

## Goal

Stabilize the frontend-facing backend contract with explicit OpenAPI operation IDs and examples, a Docker-only end-to-end test, CI coverage, and documentation for Angular integration.

## Architecture

Stage 8 does not introduce new backend subsystems. It hardens the public HTTP surface already implemented in Stage 7, then verifies that surface through a Docker-oriented smoke flow and automation. The contract work stays in `marriage-be`; `marriage-ocr` remains untouched unless a missing CLI contract is discovered, which is not expected for this stage.

## Scope

- Add explicit operation IDs and examples for the public batch, document upload, export, job, and record routes.
- Export OpenAPI from the live app into `artifacts/openapi.json`.
- Add a Docker-only end-to-end smoke test that exercises batch upload, OCR completion, record review, export creation, and download using the existing fake OCR fixture.
- Extend CI so the same checks run automatically.
- Document the frontend integration flow and the local verification commands.

## Test Strategy

- Unit tests assert OpenAPI operation IDs and request examples.
- The Docker-only smoke test runs against the containerized stack, not a local dev server.
- CI runs formatting, linting, typing, migrations, unit/integration tests, OpenAPI export, and the Docker smoke test.

## Non-goals

- No frontend code is added.
- No new OCR behavior is implemented in `marriage-ocr`.
- No authentication redesign is introduced in this stage.
