from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.imports.services.recover_stale_imports import RecoverStaleImportsService
from tests.support.imports import create_import_job, write_workbook


def test_two_watchdogs_recover_one_stale_import_without_duplicate_requeue(
    step2_session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "watchdog-race.xlsx"
    write_workbook(workbook_path, [])

    with step2_session_factory() as session:
        job = create_import_job(
            session,
            workbook_path=workbook_path,
            status="PROCESSING",
            attempt_count=1,
        )
        job.processing_token = uuid4()
        job.locked_by_worker = "stale-worker"
        job.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()

    settings = Settings(processing_stale_timeout_seconds=60)
    start_recovery = Barrier(2)

    def recover_once() -> int:
        service = RecoverStaleImportsService(
            session_factory=step2_session_factory,
            settings=settings,
        )
        start_recovery.wait(timeout=30)
        return service.recover_stale_imports(batch_size=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        recovery_counts = list(executor.map(lambda _: recover_once(), range(2)))

    with step2_session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        requeue_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == job.id)
        ).all()

    assert sorted(recovery_counts) == [0, 1]
    assert current_job is not None
    assert current_job.status == "PENDING"
    assert current_job.processing_token is None
    assert current_job.locked_by_worker is None
    assert len(requeue_events) == 1
    assert requeue_events[0].status == "PENDING"
