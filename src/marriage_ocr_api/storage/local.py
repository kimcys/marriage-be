from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from typing import cast

import filetype
from fastapi import UploadFile

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.paths import JobPaths
from marriage_ocr_api.storage.base import StorageService, StoredObject


@dataclass
class UploadValidationError(Exception):
    status_code: int
    code: str
    message: str


@dataclass(frozen=True)
class StoredUpload:
    stored_filename: str
    content_type: str
    file_size_bytes: int
    input_relative_path: str
    sha256: str


ALLOWED_EXTENSIONS_TO_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _cleanup_job_dir(job_root: Path) -> None:
    shutil.rmtree(job_root, ignore_errors=True)


def _resolve_storage_path(root: Path, key: str) -> Path:
    resolved_root = root.resolve()
    resolved = (resolved_root / key).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("storage key must remain inside the storage root")
    return resolved


@dataclass(frozen=True)
class LocalStorageService(StorageService):
    root: Path = Path("./var/storage")

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, source: Path, key: str) -> StoredObject:
        destination = _resolve_storage_path(self.root, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return StoredObject(key=key, path=destination)

    def open_read(self, key: str) -> BufferedReader:
        return _resolve_storage_path(self.root, key).open("rb")

    def materialize(self, key: str, destination: Path) -> Path:
        source = _resolve_storage_path(self.root, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def exists(self, key: str) -> bool:
        return _resolve_storage_path(self.root, key).exists()

    def delete(self, key: str) -> None:
        path = _resolve_storage_path(self.root, key)
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink(missing_ok=True)

    def signed_download_url(self, key: str, expires_seconds: int) -> str | None:
        return None


def _detect_content_type(sample: bytes, extension: str) -> str:
    expected = ALLOWED_EXTENSIONS_TO_CONTENT_TYPES[extension]
    if extension == ".pdf":
        if sample.startswith(b"%PDF-"):
            return expected
        raise UploadValidationError(
            415,
            "FILE_SIGNATURE_MISMATCH",
            "The uploaded file signature does not match the PDF extension.",
        )

    if extension in {".tif", ".tiff"} and sample[:4] in {b"II*\x00", b"MM\x00*"}:
        return expected

    guessed = filetype.guess(sample)
    if guessed is None:
        raise UploadValidationError(
            415,
            "FILE_SIGNATURE_MISMATCH",
            "The uploaded file signature does not match the file extension.",
        )
    if guessed.mime != expected:
        raise UploadValidationError(
            415,
            "FILE_SIGNATURE_MISMATCH",
            "The uploaded file signature does not match the file extension.",
        )
    return cast(str, guessed.mime)


def save_upload(upload: UploadFile, paths: JobPaths, settings: Settings) -> StoredUpload:
    filename = upload.filename or ""
    if not filename.strip():
        raise UploadValidationError(400, "INVALID_UPLOAD", "A file name is required.")

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS_TO_CONTENT_TYPES:
        raise UploadValidationError(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "Only PDF, JPEG, PNG, and TIFF files are supported.",
        )

    paths.input_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.debug_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    effective_paths = paths.with_extension(extension)
    bytes_written = 0
    digest = hashlib.sha256()
    temp_path = effective_paths.input_part_path
    final_path = effective_paths.input_source_path
    try:
        with temp_path.open("wb") as destination:
            while True:
                chunk = upload.file.read(settings.upload_chunk_bytes)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    raise UploadValidationError(
                        413,
                        "UPLOAD_TOO_LARGE",
                        "The uploaded file exceeds the maximum allowed size.",
                    )
                digest.update(chunk)
                destination.write(chunk)

        if bytes_written == 0:
            raise UploadValidationError(400, "EMPTY_FILE", "The uploaded file is empty.")

        sample = temp_path.read_bytes()[:4096]
        detected_content_type = _detect_content_type(sample, extension)
        temp_path.replace(final_path)
        return StoredUpload(
            stored_filename=final_path.name,
            content_type=detected_content_type,
            file_size_bytes=bytes_written,
            input_relative_path=effective_paths.input_relative_path,
            sha256=digest.hexdigest(),
        )
    except UploadValidationError:
        _cleanup_job_dir(paths.job_root)
        raise
    except Exception:
        _cleanup_job_dir(paths.job_root)
        raise
