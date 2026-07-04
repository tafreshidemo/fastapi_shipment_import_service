from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.imports.services.recover_stale_imports import RecoverStaleImportsService
from tests.support.imports import create_import_job, write_workbook


def test_watchdog_marks_exhausted_stale_import_failed_without_requeue_intent(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "watchdog-exhausted.xlsx"
    write_workbook(workbook_path, [])

    with session_factory() as session:
        job = create_import_job(
            session,
            workbook_path=workbook_path,
            status="PROCESSING",
            attempt_count=3,
        )
        job.max_attempts = 3
        job.processing_token = uuid4()
        job.locked_by_worker = "exhausted-worker"
        job.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()

    recovery = RecoverStaleImportsService(
        session_factory=session_factory,
        settings=Settings(processing_stale_timeout_seconds=60),
    )

    assert recovery.recover_stale_imports(batch_size=10) == 1

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        requeue_events = session.scalars(
            select(ImportDispatchOutbox).where(ImportDispatchOutbox.import_id == job.id)
        ).all()

    assert current_job is not None
    assert current_job.status == "FAILED"
    assert current_job.processing_token is None
    assert current_job.locked_by_worker is None
    assert current_job.last_failure_reason == "Worker heartbeat expired."
    assert current_job.failure_reason == (
        "Import processing failed after the maximum number of attempts."
    )
    assert current_job.finished_at is not None
    assert requeue_events == []
