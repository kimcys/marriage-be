from __future__ import annotations

import os
from pathlib import Path

import pytest

from marriage_ocr_api.storage.s3 import S3StorageService

pytestmark = pytest.mark.integration


def test_minio_storage_profile_round_trips_files(tmp_path: Path) -> None:
    endpoint_url = os.environ.get("MINIO_ENDPOINT_URL")
    bucket_name = os.environ.get("MINIO_BUCKET_NAME")
    region_name = os.environ.get("MINIO_REGION_NAME", "us-east-1")
    access_key_id = os.environ.get("MINIO_ACCESS_KEY_ID", "test")
    secret_access_key = os.environ.get("MINIO_SECRET_ACCESS_KEY", "test")
    if not endpoint_url:
        pytest.skip("MINIO profile is not enabled")

    storage = S3StorageService(
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
        region_name=region_name,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        storage_root=tmp_path,
    )

    source = tmp_path / "source.txt"
    source.write_text("hello\n", encoding="utf-8")

    stored = storage.put_file(source, "exports/123/records.txt")
    assert storage.exists(stored.key)
    assert storage.signed_download_url(stored.key, 60) is not None

    copy = tmp_path / "copy.txt"
    storage.materialize(stored.key, copy)
    assert copy.read_text(encoding="utf-8") == "hello\n"

    storage.delete(stored.key)
    assert not storage.exists(stored.key)
