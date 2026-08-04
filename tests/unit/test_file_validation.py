from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from starlette.datastructures import Headers, UploadFile

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.paths import build_job_paths
from marriage_ocr_api.storage.local import UploadValidationError, save_upload


def _upload(filename: str, data: bytes, content_type: str = "application/octet-stream") -> UploadFile:
    return UploadFile(
        file=BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _settings(storage_root: Path, max_upload_bytes: int = 1024) -> Settings:
    return Settings(storage_root=storage_root, max_upload_bytes=max_upload_bytes)


@pytest.mark.parametrize(
    ("filename", "data", "expected_content_type"),
    [
        ("register.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf"),
        ("photo.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02", "image/jpeg"),
        (
            "scan.png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01",
            "image/png",
        ),
        ("scan.tiff", b"II*\x00\x08\x00\x00\x00", "image/tiff"),
    ],
)
def test_upload_accepts_valid_signatures(
    tmp_path: Path,
    filename: str,
    data: bytes,
    expected_content_type: str,
) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174000"))

    result = save_upload(_upload(filename, data), paths, settings)

    assert result.content_type == expected_content_type
    assert result.file_size_bytes == len(data)
    stored_path = paths.with_extension(Path(result.stored_filename).suffix).input_source_path
    assert stored_path.exists()
    assert stored_path.read_bytes() == data


def test_upload_rejects_unsupported_extension(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174001"))

    with pytest.raises(UploadValidationError, match="UNSUPPORTED_FILE_TYPE"):
        save_upload(_upload("notes.txt", b"hello"), paths, settings)


def test_upload_rejects_mismatched_signature(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174002"))

    with pytest.raises(UploadValidationError, match="FILE_SIGNATURE_MISMATCH"):
        save_upload(_upload("register.pdf", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"), paths, settings)


def test_upload_stops_and_cleans_up_when_too_large(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_upload_bytes=8)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174003"))

    with pytest.raises(UploadValidationError, match="UPLOAD_TOO_LARGE"):
        save_upload(_upload("register.pdf", b"%PDF-1.4\nhello world"), paths, settings)

    assert not paths.job_root.exists()


def test_upload_rejects_empty_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174004"))

    with pytest.raises(UploadValidationError, match="EMPTY_FILE"):
        save_upload(_upload("register.pdf", b""), paths, settings)

    assert not paths.job_root.exists()
