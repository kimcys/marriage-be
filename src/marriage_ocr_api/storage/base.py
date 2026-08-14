from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    path: Path | None = None


class StorageService(Protocol):
    def put_file(self, source: Path, key: str) -> StoredObject: ...

    def open_read(self, key: str) -> BinaryIO: ...

    def materialize(self, key: str, destination: Path) -> Path: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def signed_download_url(self, key: str, expires_seconds: int) -> str | None: ...
