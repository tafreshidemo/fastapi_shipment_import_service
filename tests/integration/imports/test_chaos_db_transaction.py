from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.services.process_import import (
    ProcessImportService,
    RetryableImportProcessingError,
)
from tests.support.imports import create_import_job, write_workbook


def test_database_disconnect_mid_chunk_rolls_back_current_chunk_then_retry_completes_once(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """A DB disconnect rolls back only the active chunk and retry cleanup removes partial history."""

    workbook_path = tmp_path / "db-disconnect-mid-chunk.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-DB-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            ["SHP-DB-2", "Beta", "Austin", "Denver", 2, 20, "DELIVERED", None],
        ],
    )
    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    original_bulk_insert = ShipmentRepository.bulk_insert
    insert_calls = 0

    def disconnect_after_second_chunk_flush(self, shipments):
        nonlocal insert_calls

        inserted = original_bulk_insert(self, shipments)
        insert_calls += 1
        if insert_calls == 2:
            raise OperationalError(
                "INSERT shipments",
                {},
                ConnectionError("database connection lost"),
            )
        return inserted

    monkeypatch.setattr(
        ShipmentRepository,
        "bulk_insert",
        disconnect_after_second_chunk_flush,
    )

    service = ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=1, import_max_attempts=3),
        worker_id="db-chaos-worker",
    )

    with pytest.raises(RetryableImportProcessingError):
        service.run(job.id)

    with session_factory() as session:
        requeued_job = session.get(ImportJob, job.id)
        shipments_after_disconnect = session.scalars(
            select(Shipment)
            .where(Shipment.import_id == job.id)
            .order_by(Shipment.shipment_code)
        ).all()
        errors_after_disconnect = session.scalars(
            select(ImportError).where(ImportError.import_id == job.id)
        ).all()

    assert requeued_job is not None
    assert requeued_job.status == "PENDING"
    assert requeued_job.attempt_count == 1
    assert requeued_job.failure_reason is None
    assert requeued_job.last_failure_reason == (
        "Import processing encountered a temporary database error."
    )
    assert [shipment.shipment_code for shipment in shipments_after_disconnect] == ["SHP-DB-1"]
    assert errors_after_disconnect == []

    monkeypatch.setattr(ShipmentRepository, "bulk_insert", original_bulk_insert)
    service.run(job.id)

    with session_factory() as session:
        completed_job = session.get(ImportJob, job.id)
        final_shipments = session.scalars(
            select(Shipment)
            .where(Shipment.import_id == job.id)
            .order_by(Shipment.shipment_code)
        ).all()
        final_errors = session.scalars(
            select(ImportError).where(ImportError.import_id == job.id)
        ).all()

    assert completed_job is not None
    assert completed_job.status == "COMPLETED"
    assert completed_job.attempt_count == 2
    assert (completed_job.total_rows, completed_job.processed_rows) == (2, 2)
    assert (completed_job.success_count, completed_job.failed_count) == (2, 0)
    assert [shipment.shipment_code for shipment in final_shipments] == ["SHP-DB-1", "SHP-DB-2"]
    assert final_errors == []
