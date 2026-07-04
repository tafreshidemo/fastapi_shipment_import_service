from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from app.imports.services.recover_stale_imports import RecoverStaleImportsService
from tests.support.imports import create_import_job, write_workbook


def _create_processing_import(
    session,
    *,
    workbook_path,
    heartbeat_at: datetime,
    attempt_count: int = 1,
    max_attempts: int = 3,
) -> ImportJob:
    job = create_import_job(
        session,
        workbook_path=workbook_path,
        status="PROCESSING",
        attempt_count=attempt_count,
    )
    job.max_attempts = max_attempts
    job.processing_token = uuid4()
    job.locked_by_worker = "crashed-worker"
    job.last_heartbeat_at = heartbeat_at
    session.flush()
    return job


def test_watchdog_requeues_stale_import_once_and_leaves_fresh_import_untouched(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "watchdog.xlsx"
    write_workbook(workbook_path, [])
    now = datetime.now(UTC)

    with step2_session_factory() as session:
        stale_job = _create_processing_import(
            session,
            workbook_path=workbook_path,
            heartbeat_at=now - timedelta(minutes=10),
        )
        fresh_job = _create_processing_import(
            session,
            workbook_path=workbook_path,
            heartbeat_at=now,
        )
        session.commit()

    recovery = RecoverStaleImportsService(
        session_factory=step2_session_factory,
        settings=Settings(processing_stale_timeout_seconds=60),
    )

    assert recovery.recover_stale_imports(batch_size=10) == 1
    assert recovery.recover_stale_imports(batch_size=10) == 0

    with step2_session_factory() as session:
        recovered_job = session.get(ImportJob, stale_job.id)
        untouched_job = session.get(ImportJob, fresh_job.id)
        requeue_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == stale_job.id)
        ).all()

    assert recovered_job is not None
    assert recovered_job.status == "PENDING"
    assert recovered_job.processing_token is None
    assert recovered_job.locked_by_worker is None
    assert recovered_job.last_heartbeat_at is None
    assert recovered_job.failure_reason is None
    assert recovered_job.last_failure_reason == "Worker heartbeat expired."
    assert recovered_job.last_requeued_at is not None
    assert recovered_job.finished_at is None
    assert [(event.status, event.import_id) for event in requeue_events] == [
        ("PENDING", stale_job.id)
    ]

    assert untouched_job is not None
    assert untouched_job.status == "PROCESSING"
    assert untouched_job.processing_token is not None
    assert untouched_job.locked_by_worker == "crashed-worker"


def test_recovered_import_is_processed_once_when_duplicate_worker_delivery_occurs(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "recovered-worker-delivery.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-RECOVERED", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )

    with step2_session_factory() as session:
        job = _create_processing_import(
            session,
            workbook_path=workbook_path,
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.commit()

    recovery = RecoverStaleImportsService(
        session_factory=step2_session_factory,
        settings=Settings(processing_stale_timeout_seconds=60),
    )
    assert recovery.recover_stale_imports(batch_size=10) == 1

    worker = ProcessImportService(
        session_factory=step2_session_factory,
        settings=Settings(),
        worker_id="recovered-worker",
    )
    worker.run(job.id)
    worker.run(job.id)

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(select(Shipment).where(Shipment.import_id == job.id)).all()
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()
        outbox_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == job.id)
        ).all()

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert current_job.attempt_count == 2
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-RECOVERED"]
    assert errors == []
    assert len(outbox_events) == 1


def test_watchdog_recovers_an_anomalously_old_heartbeat(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "corrupted-heartbeat.xlsx"
    write_workbook(workbook_path, [])

    with step2_session_factory() as session:
        job = _create_processing_import(
            session,
            workbook_path=workbook_path,
            heartbeat_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        session.commit()

    recovery = RecoverStaleImportsService(
        session_factory=step2_session_factory,
        settings=Settings(processing_stale_timeout_seconds=60),
    )

    assert recovery.recover_stale_imports(batch_size=10) == 1

    with step2_session_factory() as session:
        recovered_job = session.get(ImportJob, job.id)
        requeue_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == job.id)
        ).all()

    assert recovered_job is not None
    assert recovered_job.status == "PENDING"
    assert recovered_job.last_failure_reason == "Worker heartbeat expired."
    assert len(requeue_events) == 1
