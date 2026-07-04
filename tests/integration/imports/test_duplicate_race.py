from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_global_duplicate_race_becomes_row_validation_error(
    step2_session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-RACE", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    with step2_session_factory() as session:
        target_job = create_import_job(session, workbook_path=workbook_path)
        competing_job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    original_bulk_insert = ShipmentRepository.bulk_insert
    inserted_competitor = False

    def insert_competing_shipment(self, shipments):
        nonlocal inserted_competitor
        if shipments and not inserted_competitor:
            inserted_competitor = True
            with step2_session_factory() as competing_session:
                competing_session.add(
                    Shipment(
                        import_id=competing_job.id,
                        shipment_code="SHP-RACE",
                        customer_name="Other",
                        origin_city="Miami",
                        destination_city="Dallas",
                        weight_kg=Decimal("1"),
                        price=Decimal("1"),
                        status="PENDING",
                    )
                )
                competing_session.commit()
        return original_bulk_insert(self, shipments)

    monkeypatch.setattr(ShipmentRepository, "bulk_insert", insert_competing_shipment)
    ProcessImportService(
        session_factory=step2_session_factory,
        settings=Settings(),
        worker_id="worker-a",
    ).run(target_job.id)

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, target_job.id)
        errors = session.scalars(
            select(ImportError).where(ImportError.import_id == target_job.id)
        ).all()

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert (current_job.success_count, current_job.failed_count) == (0, 1)
    assert [(error.field, error.error) for error in errors] == [
        ("shipment_code", "Shipment code already exists in the database.")
    ]
