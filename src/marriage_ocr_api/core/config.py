from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class MarriageOcrEnvSettingsSource(EnvSettingsSource):
    def prepare_field_value(
        self,
        field_name: str,
        field: FieldInfo,
        value: object,
        value_is_complex: bool,
    ) -> object:
        if field_name == "cors_origins" and isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return super().prepare_field_value(field_name, field, value, value_is_complex)


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
    ocr_config_path: Path = Path("/opt/marriage-ocr/config/production.yaml")
    ocr_timeout_seconds: int = 3600
    ocr_max_concurrent_jobs: int = 1
    ocr_stderr_api_limit: int = 1000
    marriage_ocr_git_url: str = "https://github.com/kimcys/marriage-ocr.git"
    marriage_ocr_git_ref: str = "06902e69447dae6bd47f8829842e9e68d1e96296"
    google_application_credentials: str = "/run/secrets/google-vision.json"
    gemini_api_key: str = ""

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
            dotenv_settings,
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
        mode="after",
    )
    @classmethod
    def positive_ints(cls, value: int, info: ValidationInfo) -> int:
        if value <= 0:
            field_name = (info.field_name or "value").upper()
            raise ValueError(f"{field_name} must be positive")
        return value

    @field_validator("ocr_max_concurrent_jobs", mode="after")
    @classmethod
    def require_single_concurrent_job(cls, value: int) -> int:
        if value != 1:
            raise ValueError("OCR_MAX_CONCURRENT_JOBS must equal 1 in Phase 1")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
