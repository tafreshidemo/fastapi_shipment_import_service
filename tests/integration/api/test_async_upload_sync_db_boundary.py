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
async def test_async_upload_uses_threadpool_and_plain_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(upload_dir)
    payload = b"boundary-bytes-" * 256
    event_loop_thread_name = threading.current_thread().name
    boundary_state: dict[str, object] = {
        "service_thread_name": None,
        "kwargs": None,
    }

    class FakeStorage:
        def __init__(self, upload_dir: Path, chunk_size_bytes: int, max_size_bytes: int) -> None:
            self.upload_dir = upload_dir
            self.chunk_size_bytes = chunk_size_bytes

        async def save_upload(self, *, reader, original_file_name: str) -> StoredFile:
            digest = hashlib.sha256()
            total_size = 0
            while True:
                chunk = await reader(self.chunk_size_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                total_size += len(chunk)
            return StoredFile(
                original_file_name=Path(original_file_name).name,
                stored_file_path=str(self.upload_dir / "boundary.xlsx"),
                file_size_bytes=total_size,
                sha256_hex=digest.hexdigest(),
            )

        async def delete_if_exists(self, path: Path) -> None:
            path.unlink(missing_ok=True)

    class FakeCreateImportService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_import(self, **kwargs: object) -> ImportCreateOutcome:
            boundary_state["service_thread_name"] = threading.current_thread().name
            boundary_state["kwargs"] = kwargs
            return ImportCreateOutcome(
                result=ImportCreatedResult(
                    import_id=uuid4(),
                    status="PENDING",
                    created_at=datetime.now(UTC),
                ),
                created=True,
            )

    def fail_if_published(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("request path must not publish to RabbitMQ or Celery")

    monkeypatch.setattr(imports_module, "LocalFileStorage", FakeStorage)
    monkeypatch.setattr(imports_module, "CreateImportService", FakeCreateImportService)
    async def always_zip(_path: str) -> bool:
        return True

    monkeypatch.setattr(imports_module, "_has_xlsx_zip_signature", always_zip)
    monkeypatch.setattr("app.celery_app.celery_app.send_task", fail_if_published)
    monkeypatch.setattr("app.celery_app.celery_app.apply_async", fail_if_published, raising=False)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("boundary.xlsx", payload, "application/octet-stream")},
        )

    assert response.status_code == 202
    body = response.json()
    assert isinstance(body["import_id"], str)
    assert body["status"] == "PENDING"
    assert boundary_state["service_thread_name"] is not None
    assert boundary_state["service_thread_name"] != event_loop_thread_name

    observed_kwargs = boundary_state["kwargs"]
    assert isinstance(observed_kwargs, dict)
    assert observed_kwargs["stored_file_path"] == str(upload_dir / "boundary.xlsx")
    assert observed_kwargs["idempotency_key"] is None
    assert isinstance(observed_kwargs["idempotency_fingerprint"], str)
