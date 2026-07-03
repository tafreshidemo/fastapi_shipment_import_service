from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.v1 import imports as imports_module
from app.core.settings import Settings, get_settings
from app.db.session import get_session_factory
from app.imports.dto import ImportCreatedResult
from app.imports.services.create_import import ImportCreateOutcome
from app.storage.local_file_storage import StoredFile


def _build_app(upload_dir: Path) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(upload_dir=upload_dir)
    app.dependency_overrides[get_session_factory] = lambda: object()
    return app


@pytest.mark.asyncio
async def test_upload_streams_with_configured_chunk_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(upload_dir)
    event_loop_thread_name = threading.current_thread().name
    payload = b"streaming-bytes-" * 80_000
    call_state: dict[str, object] = {
        "thread_name": None,
        "requested_sizes": [],
        "observed_kwargs": None,
    }

    class SpyStorage:
        instances: list[SpyStorage] = []

        def __init__(self, upload_dir: Path, chunk_size_bytes: int, max_size_bytes: int) -> None:
            self.upload_dir = upload_dir
            self.chunk_size_bytes = chunk_size_bytes
            self.max_size_bytes = max_size_bytes
            self.requested_sizes: list[int] = []
            self.read_sizes: list[int] = []
            SpyStorage.instances.append(self)

        async def save_upload(self, *, reader, original_file_name: str) -> StoredFile:
            digest = hashlib.sha256()
            total_size = 0
            while True:
                chunk = await reader(self.chunk_size_bytes)
                self.requested_sizes.append(self.chunk_size_bytes)
                if not chunk:
                    break
                self.read_sizes.append(len(chunk))
                digest.update(chunk)
                total_size += len(chunk)
            return StoredFile(
                original_file_name=Path(original_file_name).name,
                stored_file_path=str(self.upload_dir / "streaming.xlsx"),
                file_size_bytes=total_size,
                sha256_hex=digest.hexdigest(),
            )

        async def delete_if_exists(self, path: Path) -> None:
            path.unlink(missing_ok=True)

    class SpyCreateImportService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_import(self, **kwargs: object) -> ImportCreateOutcome:
            call_state["thread_name"] = threading.current_thread().name
            call_state["observed_kwargs"] = kwargs
            return ImportCreateOutcome(
                result=ImportCreatedResult(
                    import_id=uuid4(),
                    status="PENDING",
                    created_at=datetime.now(UTC),
                ),
                created=True,
            )

    monkeypatch.setattr(imports_module, "LocalFileStorage", SpyStorage)
    monkeypatch.setattr(imports_module, "CreateImportService", SpyCreateImportService)
    async def always_zip(_path: str) -> bool:
        return True

    monkeypatch.setattr(imports_module, "_has_xlsx_zip_signature", always_zip)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("streaming.xlsx", payload, "application/octet-stream")},
            headers={"Idempotency-Key": "streaming-key"},
        )

    assert response.status_code == 202
    assert SpyStorage.instances
    storage = SpyStorage.instances[-1]
    assert storage.chunk_size_bytes == 1024 * 1024
    assert len(storage.read_sizes) > 1
    assert all(size <= storage.chunk_size_bytes for size in storage.read_sizes)
    assert call_state["thread_name"] != event_loop_thread_name
    assert call_state["thread_name"] is not None
    observed_kwargs = call_state["observed_kwargs"]
    assert isinstance(observed_kwargs, dict)
    assert observed_kwargs["stored_file_path"] == str(upload_dir / "streaming.xlsx")
    assert observed_kwargs["idempotency_key"] == "streaming-key"
    assert isinstance(observed_kwargs["file_size_bytes"], int)
    assert isinstance(observed_kwargs["idempotency_fingerprint"], str)
