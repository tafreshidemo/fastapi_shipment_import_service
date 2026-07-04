from __future__ import annotations

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_failed_chunk_rolls_back_without_losing_prior_chunks(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            ["SHP-2", "Beta", "Austin", "Denver", 2, 20, "DELIVERED", None],
        ],
    )
    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    original_bulk_insert = ShipmentRepository.bulk_insert
    calls = 0

    def fail_after_second_flush(self, shipments):
        nonlocal calls
        calls += 1
        inserted = original_bulk_insert(self, shipments)
        if calls == 2:
            raise RuntimeError("simulated chunk failure")
        return inserted

    monkeypatch.setattr(ShipmentRepository, "bulk_insert", fail_after_second_flush)
    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=1),
        worker_id="worker-a",
    ).run(job.id)

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == job.id).order_by(Shipment.shipment_code)
        ).all()

    assert current_job is not None
    assert current_job.status == "FAILED"
    assert current_job.processed_rows == 1
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-1"]
