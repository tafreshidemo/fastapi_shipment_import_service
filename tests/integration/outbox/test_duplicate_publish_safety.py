from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_duplicate_outbox_events_do_not_duplicate_shipment_processing(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "duplicate-outbox-dispatch.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-OUTBOX-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        add_dispatch_event(session, import_id=job.id, available_at=due_at)
        add_dispatch_event(session, import_id=job.id, available_at=due_at)
        session.commit()

    worker_service = ProcessImportService(
        session_factory=session_factory,
        settings=Settings(),
        worker_id="outbox-test-worker",
    )
    dispatched_import_ids: list[UUID] = []

    def dispatch_to_worker(import_id: UUID) -> None:
        dispatched_import_ids.append(import_id)
        worker_service.run(import_id)

    publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_batch_size=10),
        dispatch_import=dispatch_to_worker,
    )

    assert publisher.publish_due_events() == 2

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(select(Shipment).where(Shipment.import_id == job.id)).all()
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()
        outbox_events = session.scalars(
            select(ImportDispatchOutbox)
            .where(ImportDispatchOutbox.import_id == job.id)
            .order_by(ImportDispatchOutbox.id)
        ).all()

    assert dispatched_import_ids == [job.id, job.id]
    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert current_job.attempt_count == 1
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-OUTBOX-1"]
    assert errors == []
    assert [event.status for event in outbox_events] == ["PUBLISHED", "PUBLISHED"]
