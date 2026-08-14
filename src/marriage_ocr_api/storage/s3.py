from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from marriage_ocr_api.storage.base import StorageService, StoredObject


@dataclass(frozen=True)
class S3StorageService(StorageService):
    bucket_name: str
    endpoint_url: str
    region_name: str
    access_key_id: str
    secret_access_key: str
    storage_root: Path = field(default_factory=lambda: Path("./var/storage"))

    def __post_init__(self) -> None:
        client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region_name,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
        )
        object.__setattr__(self, "_client", client)

    @property
    def _boto_client(self) -> Any:
        return object.__getattribute__(self, "_client")

    def ensure_bucket_exists(self) -> None:
        """Idempotently create the bucket. Call once at startup, not per-request."""
        try:
            self._boto_client.head_bucket(Bucket=self.bucket_name)
        except ClientError:
            self._boto_client.create_bucket(Bucket=self.bucket_name)

    def put_file(self, source: Path, key: str) -> StoredObject:
        self._boto_client.upload_file(str(source), self.bucket_name, key)
        return StoredObject(key=key, path=None)

    def open_read(self, key: str) -> BinaryIO:
        response = self._boto_client.get_object(Bucket=self.bucket_name, Key=key)
        return cast(BinaryIO, response["Body"])

    def materialize(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._boto_client.download_file(self.bucket_name, key, str(destination))
        return destination

    def exists(self, key: str) -> bool:
        try:
            self._boto_client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self._boto_client.delete_object(Bucket=self.bucket_name, Key=key)

    def signed_download_url(self, key: str, expires_seconds: int) -> str | None:
        return cast(
            str,
            self._boto_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expires_seconds,
            ),
        )
