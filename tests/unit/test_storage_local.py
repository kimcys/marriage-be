from __future__ import annotations

from pathlib import Path

from marriage_ocr_api.storage.local import LocalStorageService


def test_local_storage_materializes_and_deletes(tmp_path: Path) -> None:
    storage = LocalStorageService(tmp_path)
    source = tmp_path / "source.csv"
    source.write_text("hello\n", encoding="utf-8")

    stored = storage.put_file(source, "exports/123/records.csv")
    assert stored.key == "exports/123/records.csv"
    assert storage.exists(stored.key)

    destination = tmp_path / "copy.csv"
    assert storage.materialize(stored.key, destination) == destination
    assert destination.read_text(encoding="utf-8") == "hello\n"

    with storage.open_read(stored.key) as handle:
        assert handle.read() == b"hello\n"

    assert storage.signed_download_url(stored.key, expires_seconds=60) is None

    storage.delete(stored.key)
    assert not storage.exists(stored.key)
