from __future__ import annotations

from marriage_ocr_api.storage.s3 import S3StorageService


def test_s3_storage_builds_signed_url() -> None:
    storage = S3StorageService(
        bucket_name="exports",
        endpoint_url="http://minio:9000",
        region_name="us-east-1",
        access_key_id="test",
        secret_access_key="test",
    )

    signed_url = storage.signed_download_url("exports/123/records.xlsx", 60)
    assert signed_url is not None
    assert "http://minio:9000" in signed_url
    assert "exports/123/records.xlsx" in signed_url
