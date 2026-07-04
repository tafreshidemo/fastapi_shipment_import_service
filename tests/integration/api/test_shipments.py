from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.api._query_support import add_import, add_shipment, build_query_app


@pytest.mark.asyncio
async def test_shipments_filter_paginate_and_order_deterministically(
    session_factory,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with session_factory() as session:
        first_import = add_import(session)
        second_import = add_import(session)
        add_shipment(
            session,
            import_id=first_import.id,
            shipment_code="SHP-Q-1",
            customer_name="Acme North",
            origin_city="Boston",
            destination_city="Seattle",
            status="PENDING",
            created_at=now - timedelta(days=2),
        )
        matching = add_shipment(
            session,
            import_id=second_import.id,
            shipment_code="SHP-Q-2",
            customer_name="ACME South",
            origin_city="Austin",
            destination_city="Denver",
            status="DELIVERED",
            created_at=now - timedelta(days=1),
        )
        recent = add_shipment(
            session,
            import_id=second_import.id,
            shipment_code="SHP-Q-3",
            customer_name="Other",
            origin_city="Austin",
            destination_city="Dallas",
            status="IN_TRANSIT",
            created_at=now,
        )
        session.commit()

    app = build_query_app(session_factory)
    target_date = (now - timedelta(days=1)).date().isoformat()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        filtered = await client.get(
            "/api/v1/shipments",
            params={
                "status": "DELIVERED",
                "origin_city": "austin",
                "destination_city": "denver",
                "customer_name": "acme",
                "created_from": target_date,
                "created_to": target_date,
                "page": "1",
                "page_size": "20",
            },
        )
        paginated = await client.get(
            "/api/v1/shipments",
            params={"page": "1", "page_size": "2"},
        )
        invalid_status = await client.get("/api/v1/shipments", params={"status": "UNKNOWN"})
        invalid_range = await client.get(
            "/api/v1/shipments",
            params={"created_from": "2026-06-03", "created_to": "2026-06-02"},
        )
        invalid_page = await client.get("/api/v1/shipments", params={"page_size": "101"})

    assert filtered.status_code == 200
    assert [item["shipment_id"] for item in filtered.json()["items"]] == [str(matching.id)]
    assert filtered.json()["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total_items": 1,
        "total_pages": 1,
    }

    assert paginated.status_code == 200
    assert [item["shipment_id"] for item in paginated.json()["items"]] == [
        str(recent.id),
        str(matching.id),
    ]
    assert paginated.json()["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total_items": 3,
        "total_pages": 2,
    }

    for response, expected_code in (
        (invalid_status, "INVALID_FILTER"),
        (invalid_range, "INVALID_FILTER"),
        (invalid_page, "INVALID_PAGINATION"),
    ):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == expected_code
