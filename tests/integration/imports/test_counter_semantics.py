from __future__ import annotations

from decimal import Decimal

from app.core.settings import Settings
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_import_counters_follow_the_declared_row_equations(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-OK", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            [None, None, None, None, None, None, None, None],
            ["SHP-INVALID", "Beta", "Austin", "Denver", 0, 10, "PENDING", None],
            ["SHP-OK", "Gamma", "Miami", "Dallas", 1, 10, "PENDING", None],
            ["SHP-EXISTS", "Other", "Rome", "Paris", 1, 10, "DELIVERED", None],
        ],
    )
    with step2_session_factory() as session:
        existing_job = create_import_job(session, workbook_path=workbook_path)
        target_job = create_import_job(session, workbook_path=workbook_path)
        session.add(
            Shipment(
                import_id=existing_job.id,
                shipment_code="SHP-EXISTS",
                customer_name="Stored",
                origin_city="Rome",
                destination_city="Paris",
                weight_kg=Decimal("1"),
                price=Decimal("1"),
                status="PENDING",
            )
        )
        session.commit()

    ProcessImportService(
        session_factory=step2_session_factory,
        settings=Settings(processing_row_chunk_size=2),
        worker_id="worker-a",
    ).run(target_job.id)

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, target_job.id)

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert current_job.total_rows == 4
    assert current_job.processed_rows == 4
    assert current_job.success_count == 1
    assert current_job.failed_count == 3
    assert current_job.processed_rows == current_job.success_count + current_job.failed_count
