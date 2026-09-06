from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from marriage_ocr_api.core.config import Settings
from marriage_ocr_api.jobs.paths import JobPaths
from marriage_ocr_api.storage.local import (
    ALLOWED_EXTENSIONS_TO_CONTENT_TYPES,
    StoredUpload,
    UploadValidationError,
    detect_content_type,
)


def save_local_file(source_path: Path, paths: JobPaths, settings: Settings) -> StoredUpload:
    """Validate and move a file that's already fully downloaded to local disk
    (a OneDrive fetch result) into a Document's storage layout -- same
    extension/signature/size checks and StoredUpload result shape a direct
    file upload would get, just reading from a Path instead of a streamed
    FastAPI UploadFile."""
    extension = source_path.suffix.lower()
    if extension not in ALLOWED_EXTENSIONS_TO_CONTENT_TYPES:
        raise UploadValidationError(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "Only PDF, JPEG, PNG, and TIFF files are supported.",
        )

    file_size_bytes = source_path.stat().st_size
    if file_size_bytes == 0:
        raise UploadValidationError(400, "EMPTY_FILE", "The downloaded file is empty.")
    if file_size_bytes > settings.max_upload_bytes:
        raise UploadValidationError(
            413,
            "UPLOAD_TOO_LARGE",
            "The downloaded file exceeds the maximum allowed size.",
        )

    paths.input_dir.mkdir(parents=True, exist_ok=True)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.debug_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    effective_paths = paths.with_extension(extension)
    final_path = effective_paths.input_source_path

    sample = source_path.open("rb").read(4096)
    detected_content_type = detect_content_type(sample, extension)

    digest = hashlib.sha256()
    with source_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)

    shutil.move(str(source_path), final_path)

    return StoredUpload(
        stored_filename=final_path.name,
        content_type=detected_content_type,
        file_size_bytes=file_size_bytes,
        input_relative_path=effective_paths.input_relative_path,
        sha256=digest.hexdigest(),
    )
