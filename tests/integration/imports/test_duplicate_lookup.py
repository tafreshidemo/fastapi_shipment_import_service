from __future__ import annotations

from uuid import uuid4

from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.shipment_repository import ShipmentRepository


def test_duplicate_lookup_uses_real_postgresql_set_lookup(step2_session_factory) -> None:
    import_id = uuid4()

    with step2_session_factory() as session:
        session.add(
            ImportJob(
                id=import_id,
                original_file_name="imports.xlsx",
                stored_file_path="/tmp/imports.xlsx",
                file_size_bytes=10,
                content_type=None,
                idempotency_key=None,
                idempotency_fingerprint="fingerprint",
                max_attempts=3,
            )
        )
        session.add_all(
            [
                Shipment(
                    import_id=import_id,
                    shipment_code="SHP-1",
                    customer_name="Acme",
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=1,
                    price=0,
                    status="PENDING",
                ),
                Shipment(
                    import_id=import_id,
                    shipment_code="SHP-3",
                    customer_name="Beta",
                    origin_city="Austin",
                    destination_city="Denver",
                    weight_kg=2,
                    price=10,
                    status="DELIVERED",
                ),
            ]
        )
        session.commit()

    with step2_session_factory() as session:
        repository = ShipmentRepository(session)
        existing = repository.find_existing_shipment_codes({"SHP-1", "SHP-2", "SHP-3"})

    assert existing == {"SHP-1", "SHP-3"}
    assert isinstance(existing, set)
