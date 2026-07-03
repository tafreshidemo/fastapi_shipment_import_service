from __future__ import annotations

import io
import zipfile
from pathlib import Path

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


def _xlsx_bytes(marker: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", f"<workbook>{marker}</workbook>")
    return buffer.getvalue()


def _build_app(session_factory: sessionmaker, upload_dir: Path) -> FastAPI:
    app = create_app()
    settings = Settings(upload_dir=upload_dir)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return app


@pytest.mark.asyncio
async def test_same_idempotency_key_and_fingerprint_returns_same_import(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)
    payload = _xlsx_bytes("same-fingerprint")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first_response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", payload, "application/octet-stream")},
            headers={"Idempotency-Key": "import-123"},
        )
        second_response = await client.post(
            "/api/v1/imports",
            files={"file": ("imports.xlsx", payload, "application/octet-stream")},
            headers={"Idempotency-Key": "import-123"},
        )

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    first_body = first_response.json()
    second_body = second_response.json()
    assert first_body["import_id"] == second_body["import_id"]
    assert first_body["status"] == second_body["status"] == "PENDING"
    assert len(list(upload_dir.iterdir())) == 1

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 1


@pytest.mark.asyncio
async def test_same_idempotency_key_and_different_fingerprint_returns_conflict(
    step2_session_factory: sessionmaker,
    tmp_path: Path,
) -> None:
    upload_dir = tmp_path / "uploads"
    app = _build_app(step2_session_factory, upload_dir)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first_response = await client.post(
            "/api/v1/imports",
            files={
                "file": (
                    "imports.xlsx",
                    _xlsx_bytes("fingerprint-a"),
                    "application/octet-stream",
                ),
            },
            headers={"Idempotency-Key": "import-456"},
        )
        second_response = await client.post(
            "/api/v1/imports",
            files={
                "file": (
                    "imports.xlsx",
                    _xlsx_bytes("fingerprint-b"),
                    "application/octet-stream",
                ),
            },
            headers={"Idempotency-Key": "import-456"},
        )

    assert first_response.status_code == 202
    assert second_response.status_code == 409
    assert second_response.json()["error"] == {
        "code": "IDEMPOTENCY_CONFLICT",
        "message": "Idempotency-Key already exists for a different upload.",
        "details": None,
    }
    assert len(list(upload_dir.iterdir())) == 1

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 1
