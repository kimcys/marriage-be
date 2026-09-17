from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from marriage_ocr_api.storage.s3 import S3StorageService


def _storage() -> S3StorageService:
    return S3StorageService(
        bucket_name="exports",
        endpoint_url="http://minio:9000",
        region_name="us-east-1",
        access_key_id="test",
        secret_access_key="test",
    )


def test_s3_storage_builds_signed_url() -> None:
    storage = _storage()

    signed_url = storage.signed_download_url("exports/123/records.xlsx", 60)
    assert signed_url is not None
    assert "http://minio:9000" in signed_url
    assert "exports/123/records.xlsx" in signed_url


def test_put_file_forwards_content_type_so_downloads_dont_default_to_octet_stream(tmp_path: Path) -> None:
    storage = _storage()
    storage._boto_client.upload_file = Mock()  # type: ignore[attr-defined]
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg-bytes")

    storage.put_file(source, "batches/1/documents/1/input/source.jpg", content_type="image/jpeg")

    storage._boto_client.upload_file.assert_called_once_with(
        str(source),
        "exports",
        "batches/1/documents/1/input/source.jpg",
        ExtraArgs={"ContentType": "image/jpeg"},
    )


def test_put_file_without_content_type_omits_extra_args(tmp_path: Path) -> None:
    storage = _storage()
    storage._boto_client.upload_file = Mock()  # type: ignore[attr-defined]
    source = tmp_path / "source.txt"
    source.write_text("hello")

    storage.put_file(source, "exports/123/records.txt")

    storage._boto_client.upload_file.assert_called_once_with(
        str(source), "exports", "exports/123/records.txt", ExtraArgs=None
    )
