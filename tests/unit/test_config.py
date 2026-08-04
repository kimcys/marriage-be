import pytest

from marriage_ocr_api.core.config import Settings


def test_cors_origins_are_parsed_into_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:4200,https://example.com")

    settings = Settings()

    assert settings.cors_origins == ["http://localhost:4200", "https://example.com"]


def test_max_upload_bytes_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "0")

    with pytest.raises(ValueError, match="MAX_UPLOAD_BYTES"):
        Settings()


def test_ocr_max_concurrent_jobs_must_be_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_MAX_CONCURRENT_JOBS", "2")

    with pytest.raises(ValueError, match="OCR_MAX_CONCURRENT_JOBS"):
        Settings()
