from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_retry_storm_respects_backoff_and_stops_at_outbox_max_attempts(
    session_factory,
    tmp_path,
) -> None:
    """Many broker failures remain bounded by persisted retry state and maximum attempts."""

    workbook_path = tmp_path / "retry-storm-exhaustion.xlsx"
    write_workbook(workbook_path, [])

    event_count = 12
    settings = Settings(outbox_batch_size=event_count, outbox_max_attempts=3)
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with session_factory() as session:
        import_ids: list[UUID] = []
        event_ids = []
        for _ in range(event_count):
            job = create_import_job(session, workbook_path=workbook_path)
            event = add_dispatch_event(session, import_id=job.id, available_at=due_at)
            import_ids.append(job.id)
            event_ids.append(event.id)
        session.commit()

    calls: list[UUID] = []

    def broker_unavailable(import_id: UUID) -> None:
        calls.append(import_id)
        raise ConnectionError("RabbitMQ unavailable")

    publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=settings,
        dispatch_import=broker_unavailable,
    )

    for attempt in range(1, settings.outbox_max_attempts + 1):
        assert publisher.publish_due_events() == event_count

        with session_factory() as session:
            events = session.scalars(
                select(ImportDispatchOutbox)
                .where(ImportDispatchOutbox.id.in_(event_ids))
                .order_by(ImportDispatchOutbox.id)
            ).all()
            imports = session.scalars(
                select(ImportJob)
                .where(ImportJob.id.in_(import_ids))
                .order_by(ImportJob.id)
            ).all()

        assert [event.attempt_count for event in events] == [attempt] * event_count

        if attempt < settings.outbox_max_attempts:
            assert all(event.status == "PENDING" for event in events)
            assert all(event.available_at > datetime.now(UTC) for event in events)
            assert publisher.publish_due_events() == 0
            assert all(import_job.status == "PENDING" for import_job in imports)

            with session_factory() as session:
                with session.begin():
                    session.execute(
                        update(ImportDispatchOutbox)
                        .where(ImportDispatchOutbox.id.in_(event_ids))
                        .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
        else:
            assert all(event.status == "FAILED" for event in events)
            assert all(event.claim_token is None for event in events)
            assert all(import_job.status == "FAILED" for import_job in imports)
            assert all(import_job.failure_reason is not None for import_job in imports)

    assert len(calls) == event_count * settings.outbox_max_attempts
