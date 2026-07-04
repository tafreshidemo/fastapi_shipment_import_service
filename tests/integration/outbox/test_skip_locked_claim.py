from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from sqlalchemy import select

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.outbox.repositories.outbox_repository import OutboxRepository
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_parallel_publishers_claim_disjoint_batches_with_skip_locked(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "claim-load.xlsx"
    write_workbook(workbook_path, [])

    publisher_count = 4
    batch_size = 6
    event_count = publisher_count * batch_size
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with step2_session_factory() as session:
        for _ in range(event_count):
            job = create_import_job(session, workbook_path=workbook_path)
            add_dispatch_event(session, import_id=job.id, available_at=due_at)
        session.commit()

    start_claiming = Barrier(publisher_count)

    def claim_batch() -> set[object]:
        with step2_session_factory() as session:
            repository = OutboxRepository(session)
            with session.begin():
                start_claiming.wait(timeout=30)
                claimed = repository.claim_due_events(
                    batch_size=batch_size,
                    claimed_at=datetime.now(UTC),
                )
            return {event.outbox_id for event in claimed}

    with ThreadPoolExecutor(max_workers=publisher_count) as executor:
        claimed_batches = list(executor.map(lambda _: claim_batch(), range(publisher_count)))

    claimed_ids = set().union(*claimed_batches)
    assert [len(batch) for batch in claimed_batches] == [batch_size] * publisher_count
    assert len(claimed_ids) == event_count

    with step2_session_factory() as session:
        events = session.scalars(
            select(ImportDispatchOutbox).order_by(ImportDispatchOutbox.id)
        ).all()

    assert len(events) == event_count
    assert all(event.status == "PROCESSING" for event in events)
    assert all(event.claim_token is not None for event in events)
