from __future__ import annotations

from marriage_ocr_api.main import app


def test_openapi_has_stable_operation_ids_and_examples() -> None:
    spec = app.openapi()

    assert spec["paths"]["/api/v1/batches"]["post"]["operationId"] == "create_batch"
    assert spec["paths"]["/api/v1/batches/{batch_id}/documents"]["post"]["operationId"] == "upload_batch_document"
    assert spec["paths"]["/api/v1/exports"]["post"]["operationId"] == "create_export"
    assert spec["paths"]["/api/v1/records/{record_id}"]["patch"]["operationId"] == "update_record"

    batch_examples = spec["paths"]["/api/v1/batches"]["post"]["requestBody"]["content"]["application/json"]
    export_examples = spec["paths"]["/api/v1/exports"]["post"]["requestBody"]["content"]["application/json"]
    record_examples = spec["paths"]["/api/v1/records/{record_id}"]["patch"]["requestBody"]["content"][
        "application/json"
    ]

    assert "examples" in batch_examples
    assert "examples" in export_examples
    assert "examples" in record_examples
