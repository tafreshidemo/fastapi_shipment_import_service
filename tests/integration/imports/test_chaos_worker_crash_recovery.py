from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from app.imports.services.recover_stale_imports import RecoverStaleImportsService
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


class SimulatedWorkerCrash(BaseException):
    """Models a worker process ending after a committed chunk."""


def test_import_lifecycle_recovers_after_worker_crash_and_replay(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """A crash after one committed chunk recovers through watchdog and outbox replay."""

    workbook_path = tmp_path / "worker-crash-recovery.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-CRASH-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            ["SHP-CRASH-2", "Beta", "Austin", "Denver", 2, 20, "DELIVERED", None],
        ],
    )

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        add_dispatch_event(
            session,
            import_id=job.id,
            available_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.commit()

    accepted_dispatches: list[str] = []
    initial_publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_batch_size=1),
        dispatch_import=lambda import_id: accepted_dispatches.append(str(import_id)),
    )
    assert initial_publisher.publish_due_events() == 1

    crashing_worker = ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=1),
        worker_id="crashed-worker",
    )
    original_process_next_chunk = crashing_worker._process_next_chunk
    processed_chunks = 0

    def crash_after_first_committed_chunk(**kwargs: object):
        nonlocal processed_chunks

        result = original_process_next_chunk(**kwargs)
        if result is not None:
            processed_chunks += 1
            if processed_chunks == 1:
                raise SimulatedWorkerCrash("worker process exited")
        return result

    monkeypatch.setattr(
        crashing_worker,
        "_process_next_chunk",
        crash_after_first_committed_chunk,
    )

    with pytest.raises(SimulatedWorkerCrash):
        crashing_worker.run(job.id)

    with session_factory() as session:
        crashed_job = session.get(ImportJob, job.id)
        persisted_shipments = session.scalars(
            select(Shipment)
            .where(Shipment.import_id == job.id)
            .order_by(Shipment.shipment_code)
        ).all()

        assert crashed_job is not None
        assert crashed_job.status == "PROCESSING"
        assert crashed_job.processed_rows == 1
        assert [shipment.shipment_code for shipment in persisted_shipments] == ["SHP-CRASH-1"]

        session.execute(
            update(ImportJob)
            .where(ImportJob.id == job.id)
            .values(last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        session.commit()

    watchdog = RecoverStaleImportsService(
        session_factory=session_factory,
        settings=Settings(processing_stale_timeout_seconds=60),
    )
    assert watchdog.recover_stale_imports(batch_size=10) == 1

    replay_worker = ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=1),
        worker_id="replay-worker",
    )
    replay_publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_batch_size=10),
        dispatch_import=replay_worker.run,
    )
    assert replay_publisher.publish_due_events() == 1

    with session_factory() as session:
        completed_job = session.get(ImportJob, job.id)
        shipments = session.scalars(
            select(Shipment)
            .where(Shipment.import_id == job.id)
            .order_by(Shipment.shipment_code)
        ).all()
        errors = session.scalars(
            select(ImportError).where(ImportError.import_id == job.id)
        ).all()
        outbox_events = session.scalars(
            select(ImportDispatchOutbox)
            .where(ImportDispatchOutbox.import_id == job.id)
            .order_by(ImportDispatchOutbox.created_at, ImportDispatchOutbox.id)
        ).all()

    assert accepted_dispatches == [str(job.id)]
    assert completed_job is not None
    assert completed_job.status == "COMPLETED"
    assert completed_job.attempt_count == 2
    assert (completed_job.total_rows, completed_job.processed_rows) == (2, 2)
    assert (completed_job.success_count, completed_job.failed_count) == (2, 0)
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-CRASH-1", "SHP-CRASH-2"]
    assert errors == []
    assert [event.status for event in outbox_events] == ["PUBLISHED", "PUBLISHED"]
