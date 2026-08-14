from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class _CommaSeparatedCorsOriginsMixin:
    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: object,
        value_is_complex: bool,
    ) -> object:
        if field_name == "cors_origins" and isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return super().prepare_field_value(field_name, field, value, value_is_complex)  # type: ignore[misc]


class MarriageOcrEnvSettingsSource(_CommaSeparatedCorsOriginsMixin, EnvSettingsSource):
    pass


class MarriageOcrDotEnvSettingsSource(_CommaSeparatedCorsOriginsMixin, DotEnvSettingsSource):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Marriage OCR API"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4200"])
    database_url: str = "postgresql+psycopg://marriage_ocr:marriage_ocr@postgres:5432/marriage_ocr"
    storage_root: Path = Path("/app/storage")
    max_upload_bytes: int = 104857600
    upload_chunk_bytes: int = 1048576
    ocr_python_executable: Path = Path("/usr/local/bin/python")
    ocr_module: str = "marriage_ocr.cli"
    ocr_config_path_handwritten: Path = Path("/opt/marriage-ocr/config/production.yaml")
    ocr_config_path_typed: Path = Path("/opt/marriage-ocr/config/typed_borang4b.yaml")
    ocr_timeout_seconds: int = 3600
    ocr_max_concurrent_jobs: int = 1
    ocr_stderr_api_limit: int = 1000
    marriage_ocr_git_url: str = "https://github.com/kimcys/marriage-ocr.git"
    marriage_ocr_git_ref: str = "06902e69447dae6bd47f8829842e9e68d1e96296"
    google_application_credentials: str = "/run/secrets/google-vision.json"
    gemini_api_key: str = ""
    storage_backend: str = "local"
    minio_endpoint_url: str = "http://minio:9000"
    minio_bucket_name: str = "exports"
    minio_region_name: str = "us-east-1"
    minio_access_key_id: str = "minio"
    minio_secret_access_key: str = "minio123"
    valkey_url: str = "redis://valkey:6379/0"
    job_executor_backend: str = "thread_pool"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            MarriageOcrEnvSettingsSource(settings_cls),
            MarriageOcrDotEnvSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        msg = "CORS_ORIGINS must be a comma-separated string or list of origins"
        raise ValueError(msg)

    @field_validator(
        "max_upload_bytes",
        "upload_chunk_bytes",
        "ocr_timeout_seconds",
        "ocr_stderr_api_limit",
        "ocr_max_concurrent_jobs",
        mode="after",
    )
    @classmethod
    def positive_ints(cls, value: int, info: ValidationInfo) -> int:
        if value <= 0:
            field_name = (info.field_name or "value").upper()
            raise ValueError(f"{field_name} must be positive")
        return value

    @field_validator("storage_backend", mode="after")
    @classmethod
    def validate_storage_backend(cls, value: str) -> str:
        if value not in {"local", "s3"}:
            raise ValueError('STORAGE_BACKEND must be "local" or "s3"')
        return value

    @field_validator("job_executor_backend", mode="after")
    @classmethod
    def validate_job_executor_backend(cls, value: str) -> str:
        if value not in {"thread_pool", "celery"}:
            raise ValueError('JOB_EXECUTOR_BACKEND must be "thread_pool" or "celery"')
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
