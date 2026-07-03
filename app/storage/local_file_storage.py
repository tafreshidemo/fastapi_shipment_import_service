from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import aiofiles
import aiofiles.os

Reader = Callable[[int], Awaitable[bytes]]


@dataclass(frozen=True)
class StoredFile:
    original_file_name: str
    stored_file_path: str
    file_size_bytes: int
    sha256_hex: str


class LocalFileStorage:
    """Stream uploads to local storage without loading the full file into memory."""

    def __init__(self, upload_dir: Path, chunk_size_bytes: int, max_size_bytes: int) -> None:
        self._upload_dir = upload_dir
        self._chunk_size_bytes = chunk_size_bytes
        self._max_size_bytes = max_size_bytes

    async def save_upload(
        self,
        *,
        reader: Reader,
        original_file_name: str,
    ) -> StoredFile:
        await aiofiles.os.makedirs(self._upload_dir, exist_ok=True)
        suffix = Path(original_file_name).suffix.lower()
        file_token = uuid4().hex
        temp_path = self._upload_dir / f"{file_token}.tmp"
        final_path = self._upload_dir / f"{file_token}{suffix}"
        digest = hashlib.sha256()
        total_size = 0

        try:
            async with aiofiles.open(temp_path, "wb") as handle:
                while True:
                    chunk = await reader(self._chunk_size_bytes)
                    if not chunk:
                        break

                    total_size += len(chunk)
                    if total_size > self._max_size_bytes:
                        raise ValueError("Uploaded file exceeds the maximum allowed size.")

                    digest.update(chunk)
                    await handle.write(chunk)

            await aiofiles.os.replace(temp_path, final_path)
        except BaseException:
            await self.delete_if_exists(temp_path)
            await self.delete_if_exists(final_path)
            raise

        return StoredFile(
            original_file_name=original_file_name,
            stored_file_path=str(final_path),
            file_size_bytes=total_size,
            sha256_hex=digest.hexdigest(),
        )

    async def delete_if_exists(self, path: Path) -> None:
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            return
