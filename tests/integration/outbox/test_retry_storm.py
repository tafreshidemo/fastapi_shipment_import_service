from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import UUID

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_parallel_rabbitmq_disconnects_record_one_retry_per_outbox_event(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "retry-storm.xlsx"
    write_workbook(workbook_path, [])

    publisher_count = 4
    batch_size = 8
    event_count = publisher_count * batch_size
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with session_factory() as session:
        for _ in range(event_count):
            job = create_import_job(session, workbook_path=workbook_path)
            add_dispatch_event(session, import_id=job.id, available_at=due_at)
        session.commit()

    calls: list[UUID] = []
    calls_lock = Lock()
    start_publishers = Barrier(publisher_count)

    def disconnected_dispatch(import_id: UUID) -> None:
        with calls_lock:
            calls.append(import_id)
        raise ConnectionError("RabbitMQ broker disconnected")

    settings = Settings(outbox_batch_size=batch_size)

    def publish_one_batch() -> int:
        service = PublishOutboxService(
            session_factory=session_factory,
            settings=settings,
            dispatch_import=disconnected_dispatch,
        )
        start_publishers.wait(timeout=30)
        return service.publish_due_events()

    with ThreadPoolExecutor(max_workers=publisher_count) as executor:
        claimed_counts = list(executor.map(lambda _: publish_one_batch(), range(publisher_count)))

    assert sum(claimed_counts) == event_count
    assert len(calls) == event_count
    assert len(set(calls)) == event_count

    with session_factory() as session:
        events = session.scalars(
            select(ImportDispatchOutbox).order_by(ImportDispatchOutbox.id)
        ).all()
        imports = session.scalars(select(ImportJob).order_by(ImportJob.id)).all()

    assert len(events) == event_count
    assert all(event.status == "PENDING" for event in events)
    assert all(event.attempt_count == 1 for event in events)
    assert all(event.claim_token is None for event in events)
    assert all(event.claimed_at is None for event in events)
    assert all(event.available_at > due_at for event in events)
    assert all(
        event.last_error == "ConnectionError: RabbitMQ broker disconnected"
        for event in events
    )
    assert all(import_job.status == "PENDING" for import_job in imports)
