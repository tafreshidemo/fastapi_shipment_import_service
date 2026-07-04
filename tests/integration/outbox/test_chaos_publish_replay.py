from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from app.celery_app import celery_app
from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_rabbitmq_disconnect_then_publisher_replay_processes_import_once(
    step2_session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """A broker outage keeps dispatch intent durable until a later publisher replays it."""

    workbook_path = tmp_path / "broker-replay.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-BROKER-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with step2_session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        event = add_dispatch_event(session, import_id=job.id, available_at=due_at)
        session.commit()

    def broker_disconnected(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("RabbitMQ connection reset")

    monkeypatch.setattr(celery_app, "send_task", broker_disconnected)

    settings = Settings(outbox_batch_size=1)
    failed_publisher = PublishOutboxService(
        session_factory=step2_session_factory,
        settings=settings,
    )

    assert failed_publisher.publish_due_events() == 1
    assert failed_publisher.publish_due_events() == 0

    with step2_session_factory() as session:
        failed_event = session.get(ImportDispatchOutbox, event.id)
        current_job = session.get(ImportJob, job.id)

        assert failed_event is not None
        assert current_job is not None
        assert failed_event.status == "PENDING"
        assert failed_event.attempt_count == 1
        assert failed_event.available_at > due_at
        assert failed_event.last_error == "ConnectionError: RabbitMQ connection reset"
        assert current_job.status == "PENDING"

        session.execute(
            update(ImportDispatchOutbox)
            .where(ImportDispatchOutbox.id == event.id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()

    dispatched_import_ids: list[UUID] = []

    def broker_restarted(task_name: str, *, args: list[str]) -> None:
        assert task_name == "imports.process_import"
        dispatched_import_ids.append(UUID(args[0]))

    monkeypatch.setattr(celery_app, "send_task", broker_restarted)

    restarted_publisher = PublishOutboxService(
        session_factory=step2_session_factory,
        settings=settings,
    )
    assert restarted_publisher.publish_due_events() == 1

    worker = ProcessImportService(
        session_factory=step2_session_factory,
        settings=Settings(),
        worker_id="broker-replay-worker",
    )
    worker.run(job.id)
    worker.run(job.id)

    with step2_session_factory() as session:
        final_event = session.get(ImportDispatchOutbox, event.id)
        final_job = session.get(ImportJob, job.id)
        shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == job.id)
        ).all()
        errors = session.scalars(
            select(ImportError).where(ImportError.import_id == job.id)
        ).all()

    assert dispatched_import_ids == [job.id]
    assert final_event is not None
    assert final_event.status == "PUBLISHED"
    assert final_event.attempt_count == 1
    assert final_job is not None
    assert final_job.status == "COMPLETED"
    assert final_job.attempt_count == 1
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-BROKER-1"]
    assert errors == []
