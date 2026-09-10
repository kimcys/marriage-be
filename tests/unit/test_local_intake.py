from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.paths import build_job_paths
from marriage_ocr_api.onedrive.local_intake import save_local_file
from marriage_ocr_api.storage.local import UploadValidationError


def _settings(storage_root: Path, max_upload_bytes: int = 1024) -> Settings:
    return Settings(storage_root=storage_root, max_upload_bytes=max_upload_bytes)


def _source_file(tmp_path: Path, filename: str, data: bytes) -> Path:
    source_path = tmp_path / "downloaded" / filename
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(data)
    return source_path


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
def test_save_local_file_accepts_valid_signatures(
    tmp_path: Path,
    filename: str,
    data: bytes,
    expected_content_type: str,
) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174000"))
    source_path = _source_file(tmp_path, filename, data)

    result = save_local_file(source_path, paths, settings)

    assert result.content_type == expected_content_type
    assert result.file_size_bytes == len(data)
    stored_path = paths.with_extension(Path(result.stored_filename).suffix).input_source_path
    assert stored_path.exists()
    assert stored_path.read_bytes() == data
    # The downloaded file is copied into the document's storage layout,
    # not moved -- the original is left in place at the source path so a
    # submission retry can still find and re-ingest it if something after
    # this call fails (see save_local_file's docstring).
    assert source_path.exists()
    assert source_path.read_bytes() == data


def test_save_local_file_rejects_unsupported_extension(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174001"))
    source_path = _source_file(tmp_path, "notes.txt", b"hello")

    with pytest.raises(UploadValidationError, match="UNSUPPORTED_FILE_TYPE"):
        save_local_file(source_path, paths, settings)


def test_save_local_file_rejects_mismatched_signature(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174002"))
    source_path = _source_file(tmp_path, "register.pdf", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    with pytest.raises(UploadValidationError, match="FILE_SIGNATURE_MISMATCH"):
        save_local_file(source_path, paths, settings)


def test_save_local_file_rejects_file_too_large(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_upload_bytes=8)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174003"))
    source_path = _source_file(tmp_path, "register.pdf", b"%PDF-1.4\nhello world")

    with pytest.raises(UploadValidationError, match="UPLOAD_TOO_LARGE"):
        save_local_file(source_path, paths, settings)


def test_save_local_file_rejects_empty_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    paths = build_job_paths(tmp_path, UUID("123e4567-e89b-12d3-a456-426614174004"))
    source_path = _source_file(tmp_path, "register.pdf", b"")

    with pytest.raises(UploadValidationError, match="EMPTY_FILE"):
        save_local_file(source_path, paths, settings)
