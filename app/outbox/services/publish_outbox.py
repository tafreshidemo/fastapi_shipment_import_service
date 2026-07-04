from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.celery_app import celery_app
from app.core.settings import Settings, get_settings
from app.db.session import get_session_factory
from app.imports.repositories.import_repository import ImportRepository
from app.outbox.repositories.outbox_repository import (
    ClaimedOutboxEvent,
    OutboxRepository,
    StaleOutboxClaim,
)

logger = logging.getLogger(__name__)

_DISPATCH_EXHAUSTED_REASON = "Import processing never started because dispatch was exhausted."
_STALE_CLAIM_REASON = "Outbox publisher claim expired."


class PublishOutboxService:
    """Publish import dispatch intents outside database locks.

    Refinement made: this service owns dispatch retry policy while
    OutboxRepository remains limited to persistence.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        dispatch_import: Callable[[UUID], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._dispatch_import = dispatch_import or self._dispatch_with_celery

    def publish_due_events(self) -> int:
        """Claim a bounded batch, publish it, then persist each outcome."""
        claimed_events = self._claim_due_events()

        for claimed_event in claimed_events:
            try:
                self._dispatch_import(claimed_event.import_id)
            except Exception as exc:
                self._record_publish_failure(claimed_event, exc)
            else:
                self._record_publish_success(claimed_event)

        return len(claimed_events)

    def recover_stale_claims(self) -> int:
        """Return publisher claims abandoned before a publish result was saved."""
        recovered_at = datetime.now(UTC)
        stale_before = recovered_at - timedelta(
            seconds=self._settings.outbox_stale_timeout_seconds
        )

        with self._session_factory() as session:
            outbox_repository = OutboxRepository(session)
            import_repository = ImportRepository(session)
            with session.begin():
                stale_claims = outbox_repository.lock_stale_claims(
                    batch_size=self._settings.outbox_batch_size,
                    stale_before=stale_before,
                )
                exhausted_import_ids = self._recover_stale_claims_in_transaction(
                    stale_claims=stale_claims,
                    outbox_repository=outbox_repository,
                    import_repository=import_repository,
                    recovered_at=recovered_at,
                )

        if exhausted_import_ids:
            logger.error(
                "Outbox dispatch exhausted for imports",
                extra={"import_ids": [str(import_id) for import_id in exhausted_import_ids]},
            )
        return len(exhausted_import_ids)

    def _recover_stale_claims_in_transaction(
        self,
        *,
        stale_claims: list[StaleOutboxClaim],
        outbox_repository: OutboxRepository,
        import_repository: ImportRepository,
        recovered_at: datetime,
    ) -> list[UUID]:
        exhausted_import_ids: list[UUID] = []
        for stale_claim in stale_claims:
            if stale_claim.attempt_count >= self._settings.outbox_max_attempts:
                failed = outbox_repository.mark_stale_claim_failed(
                    stale_claim=stale_claim,
                    error_message=_STALE_CLAIM_REASON,
                )
                if failed:
                    exhausted_import_ids.append(stale_claim.import_id)
                    import_repository.fail_pending_import_after_dispatch_exhausted(
                        import_id=stale_claim.import_id,
                        reason=_DISPATCH_EXHAUSTED_REASON,
                    )
                continue

            outbox_repository.requeue_stale_claim(
                stale_claim=stale_claim,
                recovered_at=recovered_at,
                error_message=_STALE_CLAIM_REASON,
            )
        return exhausted_import_ids

    def _claim_due_events(self) -> list[ClaimedOutboxEvent]:
        with self._session_factory() as session:
            repository = OutboxRepository(session)
            with session.begin():
                return repository.claim_due_events(
                    batch_size=self._settings.outbox_batch_size,
                    claimed_at=datetime.now(UTC),
                )

    def _record_publish_success(self, claimed_event: ClaimedOutboxEvent) -> None:
        with self._session_factory() as session:
            repository = OutboxRepository(session)
            with session.begin():
                marked_published = repository.mark_published(claimed_event=claimed_event)

        if marked_published:
            logger.info(
                "Import dispatch published",
                extra={"import_id": str(claimed_event.import_id)},
            )

    def _record_publish_failure(
        self,
        claimed_event: ClaimedOutboxEvent,
        exc: Exception,
    ) -> None:
        failed_at = datetime.now(UTC)
        error_message = _safe_error_message(exc)
        attempt_count = claimed_event.attempt_count + 1
        exhausted = attempt_count >= self._settings.outbox_max_attempts
        available_at = (
            None
            if exhausted
            else failed_at
            + timedelta(seconds=self._settings.outbox_retry_delays_seconds[attempt_count - 1])
        )

        with self._session_factory() as session:
            outbox_repository = OutboxRepository(session)
            import_repository = ImportRepository(session)
            with session.begin():
                failure_recorded = outbox_repository.mark_publish_failure(
                    claimed_event=claimed_event,
                    attempt_count=attempt_count,
                    error_message=error_message,
                    status="FAILED" if exhausted else "PENDING",
                    available_at=available_at,
                    claimed_at=failed_at if exhausted else None,
                )
                if failure_recorded and exhausted:
                    import_repository.fail_pending_import_after_dispatch_exhausted(
                        import_id=claimed_event.import_id,
                        reason=_DISPATCH_EXHAUSTED_REASON,
                    )

        if not failure_recorded:
            return

        if exhausted:
            logger.error(
                "Import dispatch exhausted",
                extra={"import_id": str(claimed_event.import_id)},
            )
            return

        logger.warning(
            "Import dispatch failed and was scheduled for retry",
            extra={"import_id": str(claimed_event.import_id)},
        )

    @staticmethod
    def _dispatch_with_celery(import_id: UUID) -> None:
        celery_app.send_task("imports.process_import", args=[str(import_id)])


def _safe_error_message(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".strip()
    return message[:1000] or type(exc).__name__


def run_outbox_publisher() -> None:
    """Run the dedicated synchronous publisher process."""
    settings = get_settings()
    service = PublishOutboxService(
        session_factory=get_session_factory(),
        settings=settings,
    )

    while True:
        service.recover_stale_claims()
        service.publish_due_events()
        time.sleep(settings.outbox_poll_interval_seconds)


if __name__ == "__main__":
    run_outbox_publisher()
