from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.api._query_support import add_import, add_import_error, build_query_app


@pytest.mark.asyncio
async def test_import_errors_use_deterministic_database_pagination(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session:
        job = add_import(session)
        first = add_import_error(
            session,
            import_id=job.id,
            row_number=8,
            field="status",
            error="Status is invalid.",
        )
        second = add_import_error(
            session,
            import_id=job.id,
            row_number=8,
            field="shipment_code",
            error="Shipment code already exists.",
        )
        third = add_import_error(
            session,
            import_id=job.id,
            row_number=11,
            field="price",
            error="Price must be non-negative.",
        )
        session.commit()

    app = build_query_app(step2_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first_page = await client.get(
            f"/api/v1/imports/{job.id}/errors",
            params={"page": "1", "page_size": "2"},
        )
        second_page = await client.get(
            f"/api/v1/imports/{job.id}/errors",
            params={"page": "2", "page_size": "2"},
        )
        invalid_page = await client.get(
            f"/api/v1/imports/{job.id}/errors",
            params={"page_size": "101"},
        )

    assert first_page.status_code == 200
    first_page_expected_rows = sorted((first, second), key=lambda error_row: error_row.id.int)
    assert first_page.json()["items"] == [
        {
            "row_number": error_row.row_number,
            "field": error_row.field,
            "error": error_row.error,
        }
        for error_row in first_page_expected_rows
    ]
    assert first_page.json()["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total_items": 3,
        "total_pages": 2,
    }
    assert second_page.status_code == 200
    assert second_page.json()["items"] == [
        {
            "row_number": 11,
            "field": third.field,
            "error": third.error,
        }
    ]
    assert invalid_page.status_code == 400
    assert invalid_page.json()["error"]["code"] == "INVALID_PAGINATION"
