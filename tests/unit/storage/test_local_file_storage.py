from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.storage import local_file_storage as local_file_storage_module
from app.storage.local_file_storage import LocalFileStorage, UploadTooLargeError


@pytest.mark.asyncio
async def test_local_file_storage_streams_and_hashes_exact_bytes(tmp_path: Path) -> None:
    payload = (b"shipment-import-bytes-" * 256) + b"tail"
    requested_sizes: list[int] = []
    offset = 0

    async def reader(size: int) -> bytes:
        nonlocal offset
        requested_sizes.append(size)
        chunk = payload[offset : offset + size]
        offset += len(chunk)
        return chunk

    storage = LocalFileStorage(tmp_path, chunk_size_bytes=128, max_size_bytes=len(payload) + 1)
    stored_file = await storage.save_upload(
        reader=reader,
        original_file_name="../nested/imports.xlsx",
    )

    assert stored_file.original_file_name == "imports.xlsx"
    assert stored_file.file_size_bytes == len(payload)
    assert stored_file.sha256_hex == hashlib.sha256(payload).hexdigest()
    assert Path(stored_file.stored_file_path).read_bytes() == payload
    assert requested_sizes and all(size == 128 for size in requested_sizes)


@pytest.mark.asyncio
async def test_local_file_storage_cleans_up_on_size_failure(tmp_path: Path) -> None:
    payload = b"x" * 128

    async def reader(_: int) -> bytes:
        return payload

    storage = LocalFileStorage(tmp_path, chunk_size_bytes=64, max_size_bytes=64)

    with pytest.raises(UploadTooLargeError, match="maximum allowed size"):
        await storage.save_upload(reader=reader, original_file_name="imports.xlsx")

    assert list(tmp_path.iterdir()) == []


def test_local_file_storage_module_does_not_import_fastapi() -> None:
    source = Path(local_file_storage_module.__file__).read_text()
    assert "FastAPI" not in source
    assert "UploadFile" not in source
