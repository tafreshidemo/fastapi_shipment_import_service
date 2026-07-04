from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


class SimulatedPublisherCrash(RuntimeError):
    pass


def test_crash_after_broker_publish_replays_the_same_outbox_event(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    workbook_path = tmp_path / "publisher-crash.xlsx"
    write_workbook(workbook_path, [])

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        event = add_dispatch_event(session, import_id=job.id)
        session.commit()

    dispatched_import_ids = []

    def record_dispatch(import_id) -> None:
        dispatched_import_ids.append(import_id)

    crashing_publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_stale_timeout_seconds=1),
        dispatch_import=record_dispatch,
    )

    def crash_before_publish_result(_claimed_event) -> None:
        raise SimulatedPublisherCrash("publisher exited after broker acceptance")

    monkeypatch.setattr(crashing_publisher, "_record_publish_success", crash_before_publish_result)

    with pytest.raises(SimulatedPublisherCrash):
        crashing_publisher.publish_due_events()

    with session_factory() as session:
        crashed_event = session.get(ImportDispatchOutbox, event.id)

    assert crashed_event is not None
    assert crashed_event.status == "PROCESSING"
    assert crashed_event.published_at is None
    assert crashed_event.claim_token is not None

    with session_factory() as session:
        with session.begin():
            session.execute(
                update(ImportDispatchOutbox)
                .where(ImportDispatchOutbox.id == event.id)
                .values(claimed_at=datetime.now(UTC) - timedelta(seconds=10))
            )

    recovery_service = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_stale_timeout_seconds=1),
        dispatch_import=record_dispatch,
    )
    recovery_service.recover_stale_claims()

    with session_factory() as session:
        recovered_event = session.get(ImportDispatchOutbox, event.id)

    assert recovered_event is not None
    assert recovered_event.status == "PENDING"
    assert recovered_event.claim_token is None
    assert recovered_event.attempt_count == 0
    assert recovered_event.last_error == "Outbox publisher claim expired."

    replay_publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_stale_timeout_seconds=1),
        dispatch_import=record_dispatch,
    )
    assert replay_publisher.publish_due_events() == 1

    with session_factory() as session:
        outbox_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == job.id)
        ).all()

    assert dispatched_import_ids == [job.id, job.id]
    assert len(outbox_events) == 1
    assert outbox_events[0].status == "PUBLISHED"
