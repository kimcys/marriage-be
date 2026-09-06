from __future__ import annotations

import shutil
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from typing import cast

import filetype

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


def detect_content_type(sample: bytes, extension: str) -> str:
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
