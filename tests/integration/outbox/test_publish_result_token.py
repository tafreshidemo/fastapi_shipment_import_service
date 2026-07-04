from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.outbox.repositories.outbox_repository import OutboxRepository
from tests.support.imports import create_import_job, write_workbook
from tests.support.outbox import add_dispatch_event


def test_stale_publish_result_cannot_overwrite_a_reclaimed_outbox_event(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "publish-token.xlsx"
    write_workbook(workbook_path, [])
    due_at = datetime.now(UTC) - timedelta(seconds=1)

    with step2_session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        event = add_dispatch_event(session, import_id=job.id, available_at=due_at)
        session.commit()

    with step2_session_factory() as session:
        repository = OutboxRepository(session)
        with session.begin():
            claimed_event = repository.claim_due_events(
                batch_size=1,
                claimed_at=datetime.now(UTC),
            )[0]

    replacement_token = uuid4()
    with step2_session_factory() as session:
        with session.begin():
            current_event = session.get(ImportDispatchOutbox, event.id)
            assert current_event is not None
            current_event.claim_token = replacement_token
            current_event.claimed_at = datetime.now(UTC)

    with step2_session_factory() as session:
        repository = OutboxRepository(session)
        with session.begin():
            assert not repository.mark_published(claimed_event=claimed_event)

    with step2_session_factory() as session:
        current_event = session.scalar(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.id == event.id)
        )

    assert current_event is not None
    assert current_event.status == "PROCESSING"
    assert current_event.claim_token == replacement_token
    assert current_event.published_at is None
