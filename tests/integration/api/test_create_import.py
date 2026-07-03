from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.core.settings import Settings, get_settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.db.session import get_session_factory
from app.imports.services.create_import import DatabaseWriteError, ImportCreateOutcome


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"></Types>")
    return buffer.getvalue()


def _build_app(
    session_factory: sessionmaker,
    upload_dir: Path,
    **settings_overrides: object,
) -> FastAPI:
    app = create_app()
    settings = Settings(upload_dir=upload_dir, **settings_overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return app


def _response_error(response_json: dict[str, object]) -> dict[str, object]:
    return response_json["error"]


@pytest.mark.asyncio
async def test_valid_xlsx_transport_returns_202_and_persists_import(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", _xlsx_bytes(), "application/octet-stream")},
        )

    assert response.status_code == 202
    body = response.json()
    assert UUID(body["import_id"])
    assert body["status"] == "PENDING"
    assert isinstance(body["created_at"], str)

    with step2_session_factory() as session:
        job = session.get(ImportJob, UUID(body["import_id"]))
        assert job is not None
        assert job.status == "PENDING"
        assert job.content_type == "application/octet-stream"
        assert Path(job.stored_file_path).exists()
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 1

    assert any(upload_dir.iterdir())


@pytest.mark.asyncio
async def test_non_xlsx_transport_is_rejected(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.txt", _xlsx_bytes(), "application/octet-stream")},
        )

    assert response.status_code == 400
    assert _response_error(response.json()) == {
        "code": "INVALID_FILE_FORMAT",
        "message": "Uploaded file must be a .xlsx file.",
        "details": None,
    }

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0

    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_multipart_file_is_rejected(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/v1/imports", data={})

    assert response.status_code == 400
    assert _response_error(response.json()) == {
        "code": "INVALID_FILE_FORMAT",
        "message": "Uploaded file must be provided as multipart form data.",
        "details": None,
    }

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0

    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_size_limit_is_enforced(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(
        step2_session_factory,
        upload_dir,
        upload_read_chunk_size_bytes=32,
        max_upload_size_bytes=64,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", b"x" * 128, "application/octet-stream")},
        )

    assert response.status_code == 413
    assert _response_error(response.json()) == {
        "code": "FILE_TOO_LARGE",
        "message": "Uploaded file exceeds the maximum allowed size.",
        "details": None,
    }

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0

    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_invalid_zip_signature_is_rejected(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", b"not-a-zip", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert _response_error(response.json()) == {
        "code": "INVALID_FILE_FORMAT",
        "message": "Uploaded file must have a valid .xlsx ZIP signature.",
        "details": None,
    }

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0

    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_pre_commit_failure_cleans_stored_file(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    class FailingCreateImportService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_import(self, **_kwargs: object) -> ImportCreateOutcome:
            raise DatabaseWriteError("boom")

    monkeypatch.setattr("app.api.v1.imports.CreateImportService", FailingCreateImportService)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", _xlsx_bytes(), "application/octet-stream")},
        )

    assert response.status_code == 500
    assert _response_error(response.json()) == {
        "code": "DATABASE_ERROR",
        "message": "Failed to persist the import.",
        "details": None,
    }

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0

    assert list(upload_dir.iterdir()) == []
