from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.outbox.services.publish_outbox import PublishOutboxService
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_repeated_broker_failures_exhaust_outbox_and_fail_pending_import(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "outbox-max-attempts.xlsx"
    write_workbook(workbook_path, [])

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        event = add_dispatch_event(session, import_id=job.id)
        session.commit()

    dispatch_calls: list[UUID] = []

    def disconnected_dispatch(import_id: UUID) -> None:
        dispatch_calls.append(import_id)
        raise ConnectionError("RabbitMQ is unavailable")

    settings = Settings(outbox_max_attempts=5, outbox_retry_delays_seconds=(2, 4, 8, 16))
    publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=settings,
        dispatch_import=disconnected_dispatch,
    )

    for expected_attempt in range(1, settings.outbox_max_attempts + 1):
        attempt_started_at = datetime.now(UTC)
        assert publisher.publish_due_events() == 1

        with session_factory() as session:
            current_event = session.get(ImportDispatchOutbox, event.id)
            current_job = session.get(ImportJob, job.id)
            assert current_event is not None
            assert current_job is not None

            assert current_event.attempt_count == expected_attempt
            if expected_attempt < settings.outbox_max_attempts:
                expected_delay = settings.outbox_retry_delays_seconds[expected_attempt - 1]
                actual_delay = (current_event.available_at - attempt_started_at).total_seconds()
                assert current_event.status == "PENDING"
                assert current_event.claim_token is None
                assert expected_delay - 1 <= actual_delay <= expected_delay + 1
                assert current_job.status == "PENDING"
                current_event.available_at = datetime.now(UTC)
                session.commit()
            else:
                assert current_event.status == "FAILED"
                assert current_event.claim_token is None
                assert current_job.status == "FAILED"
                assert current_job.failure_reason == (
                    "Import processing never started because dispatch was exhausted."
                )
                assert current_job.last_failure_reason == current_job.failure_reason

    assert dispatch_calls == [job.id] * settings.outbox_max_attempts


def test_dispatch_exhaustion_does_not_overwrite_an_import_already_processing(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "outbox-processing-race.xlsx"
    write_workbook(workbook_path, [])

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path, status="PROCESSING")
        job.processing_token = UUID("11111111-1111-1111-1111-111111111111")
        job.locked_by_worker = "active-worker"
        event = add_dispatch_event(session, import_id=job.id, attempt_count=4)
        session.commit()

    def disconnected_dispatch(_import_id: UUID) -> None:
        raise ConnectionError("RabbitMQ is unavailable")

    publisher = PublishOutboxService(
        session_factory=session_factory,
        settings=Settings(outbox_max_attempts=5),
        dispatch_import=disconnected_dispatch,
    )

    assert publisher.publish_due_events() == 1

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        current_event = session.get(ImportDispatchOutbox, event.id)

    assert current_job is not None
    assert current_job.status == "PROCESSING"
    assert current_job.failure_reason is None
    assert current_job.processing_token == UUID("11111111-1111-1111-1111-111111111111")
    assert current_event is not None
    assert current_event.status == "FAILED"
    assert current_event.attempt_count == 5
