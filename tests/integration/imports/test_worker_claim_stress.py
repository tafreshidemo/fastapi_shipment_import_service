from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_eight_workers_compete_for_one_import_without_double_processing(
    step2_session_factory,
    tmp_path,
) -> None:
    """Concurrent duplicate delivery leaves exactly one database-backed worker owner."""

    workbook_path = tmp_path / "worker-claim-stress.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-STRESS-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    with step2_session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    worker_count = 8
    start_workers = Barrier(worker_count)

    def process_once(worker_number: int) -> None:
        service = ProcessImportService(
            session_factory=step2_session_factory,
            settings=Settings(),
            worker_id=f"stress-worker-{worker_number}",
        )
        start_workers.wait(timeout=30)
        service.run(job.id)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(process_once, range(worker_count)))

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == job.id)
        ).all()
        errors = session.scalars(
            select(ImportError).where(ImportError.import_id == job.id)
        ).all()

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert current_job.attempt_count == 1
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-STRESS-1"]
    assert errors == []
