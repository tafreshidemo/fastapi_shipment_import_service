from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models.import_job import ImportJob
from app.domain.import_states import ImportStatus
from app.outbox.repositories.outbox_repository import OutboxRepository

logger = logging.getLogger(__name__)

_STALE_HEARTBEAT_REASON = "Worker heartbeat expired."
_MAX_ATTEMPTS_REASON = "Import processing failed after the maximum number of attempts."


class RecoverStaleImportsService:
    """Recover abandoned processing imports from persisted ownership state.

    Refinement made: stale recovery is separate from worker execution and
    creates replacement dispatch intent only inside the recovery transaction.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def recover_stale_imports(self, *, batch_size: int) -> int:
        """Recover a bounded batch of stale PROCESSING imports."""
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=self._settings.processing_stale_timeout_seconds)

        with self._session_factory() as session:
            outbox_repository = OutboxRepository(session)
            with session.begin():
                stale_imports = session.scalars(
                    select(ImportJob)
                    .where(
                        ImportJob.status == ImportStatus.PROCESSING,
                        ImportJob.last_heartbeat_at < stale_before,
                    )
                    .order_by(
                        ImportJob.last_heartbeat_at.asc(),
                        ImportJob.id.asc(),
                    )
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                ).all()

                for import_job in stale_imports:
                    if import_job.attempt_count >= import_job.max_attempts:
                        self._mark_failed(import_job, now)
                    else:
                        self._requeue_with_dispatch_intent(
                            import_job=import_job,
                            outbox_repository=outbox_repository,
                            now=now,
                        )

        if stale_imports:
            logger.warning("Recovered stale imports", extra={"count": len(stale_imports)})
        return len(stale_imports)

    @staticmethod
    def _mark_failed(import_job: ImportJob, now: datetime) -> None:
        import_job.status = ImportStatus.FAILED
        import_job.processing_token = None
        import_job.locked_by_worker = None
        import_job.last_failure_reason = _STALE_HEARTBEAT_REASON
        import_job.failure_reason = _MAX_ATTEMPTS_REASON
        import_job.finished_at = now

    @staticmethod
    def _requeue_with_dispatch_intent(
        *,
        import_job: ImportJob,
        outbox_repository: OutboxRepository,
        now: datetime,
    ) -> None:
        import_job.status = ImportStatus.PENDING
        import_job.processing_token = None
        import_job.locked_by_worker = None
        import_job.failure_reason = None
        import_job.last_failure_reason = _STALE_HEARTBEAT_REASON
        import_job.last_requeued_at = now
        import_job.finished_at = None
        import_job.last_heartbeat_at = None
        outbox_repository.create_pending_dispatch_intent(
            import_id=import_job.id,
            available_at=now,
        )
