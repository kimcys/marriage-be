from __future__ import annotations

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.storage.base import StorageService
from marriage_ocr_api.storage.local import LocalStorageService
from marriage_ocr_api.storage.s3 import S3StorageService


def get_storage_service(settings: Settings) -> StorageService:
    if settings.storage_backend == "s3":
        return S3StorageService(
            bucket_name=settings.minio_bucket_name,
            endpoint_url=settings.minio_endpoint_url,
            region_name=settings.minio_region_name,
            access_key_id=settings.minio_access_key_id,
            secret_access_key=settings.minio_secret_access_key,
            storage_root=settings.storage_root,
        )
    return LocalStorageService(settings.storage_root)
