from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.import_claim_repository import ImportClaimRepository
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_only_one_worker_claims_a_locked_pending_import(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(workbook_path, [])

    with step2_session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    first_session = step2_session_factory()
    second_session = step2_session_factory()
    try:
        with first_session.begin():
            first_claim = ImportClaimRepository(first_session).claim_pending_import(
                import_id=job.id,
                processing_token=uuid4(),
                worker_id="worker-a",
            )
            assert first_claim is not None

            with second_session.begin():
                second_claim = ImportClaimRepository(second_session).claim_pending_import(
                    import_id=job.id,
                    processing_token=uuid4(),
                    worker_id="worker-b",
                )
                assert second_claim is None

        with second_session.begin():
            later_claim = ImportClaimRepository(second_session).claim_pending_import(
                import_id=job.id,
                processing_token=uuid4(),
                worker_id="worker-b",
            )
        assert later_claim is None
    finally:
        first_session.close()
        second_session.close()


def test_duplicate_task_delivery_processes_one_import_once(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "duplicate-delivery.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-DELIVERY", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    with step2_session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    service = ProcessImportService(
        session_factory=step2_session_factory,
        settings=Settings(),
        worker_id="worker-a",
    )
    service.run(job.id)
    service.run(job.id)

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(select(Shipment).where(Shipment.import_id == job.id)).all()
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert current_job.attempt_count == 1
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-DELIVERY"]
    assert errors == []
