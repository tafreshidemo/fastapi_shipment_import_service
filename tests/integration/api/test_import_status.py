from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.api._query_support import add_import, build_query_app


@pytest.mark.asyncio
async def test_import_status_exposes_the_contract_and_failure_fields(
    session_factory,
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        pending = add_import(
            session,
            status="PENDING",
            started_at=now,
            last_failure_reason="Worker connection failed during attempt 1.",
        )
        completed = add_import(
            session,
            status="COMPLETED",
            started_at=now,
            finished_at=now,
            last_failure_reason="A prior recoverable failure.",
        )
        failed = add_import(
            session,
            status="FAILED",
            started_at=now,
            finished_at=now,
            last_failure_reason="Workbook cannot be opened.",
            failure_reason="Import processing failed after the maximum number of attempts.",
        )
        session.commit()

    app = build_query_app(session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        pending_response = await client.get(f"/api/v1/imports/{pending.id}")
        completed_response = await client.get(f"/api/v1/imports/{completed.id}")
        failed_response = await client.get(f"/api/v1/imports/{failed.id}")

    assert pending_response.status_code == 200
    assert completed_response.status_code == 200
    assert failed_response.status_code == 200

    required_fields = {
        "import_id",
        "status",
        "total_rows",
        "processed_rows",
        "success_count",
        "failed_count",
        "created_at",
        "started_at",
        "finished_at",
    }

    pending_body = pending_response.json()
    completed_body = completed_response.json()
    failed_body = failed_response.json()

    assert required_fields.issubset(pending_body)
    assert pending_body["last_failure_reason"] == "Worker connection failed during attempt 1."
    assert "failure_reason" not in pending_body

    assert required_fields.issubset(completed_body)
    assert "last_failure_reason" not in completed_body
    assert "failure_reason" not in completed_body

    assert required_fields.issubset(failed_body)
    assert failed_body["last_failure_reason"] == "Workbook cannot be opened."
    assert failed_body["failure_reason"] == (
        "Import processing failed after the maximum number of attempts."
    )

    for payload in (pending_body, completed_body, failed_body):
        assert "processing_token" not in payload
        assert "locked_by_worker" not in payload


@pytest.mark.asyncio
async def test_import_status_not_found_uses_the_standard_error_contract(
    session_factory,
) -> None:
    app = build_query_app(session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/v1/imports/68aa9fe0-2884-4271-a11d-886cd440c27f")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "IMPORT_NOT_FOUND",
            "message": "Import job was not found.",
            "details": None,
        }
    }
