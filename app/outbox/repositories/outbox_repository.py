from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    """A committed outbox claim that can be published outside a transaction."""

    outbox_id: UUID
    import_id: UUID
    claim_token: UUID
    attempt_count: int


@dataclass(frozen=True, slots=True)
class StaleOutboxClaim:
    """A stale publisher claim locked for recovery in the current transaction."""

    outbox_id: UUID
    import_id: UUID
    claim_token: UUID | None
    attempt_count: int


class OutboxRepository:
    """Persist import-dispatch rows without owning transactions.

    Refinement made: retry and exhaustion decisions remain in
    PublishOutboxService; this repository only executes persistence operations.
    """
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_due_events(
        self,
        *,
        batch_size: int,
        claimed_at: datetime,
    ) -> list[ClaimedOutboxEvent]:
        """Claim due events without waiting for another publisher's locks."""
        events = self._session.scalars(
            select(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.status == "PENDING",
                ImportDispatchOutbox.available_at <= claimed_at,
            )
            .order_by(
                ImportDispatchOutbox.available_at.asc(),
                ImportDispatchOutbox.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()

        claimed_events: list[ClaimedOutboxEvent] = []
        for event in events:
            claim_token = uuid4()
            event.status = "PROCESSING"
            event.claim_token = claim_token
            event.claimed_at = claimed_at
            claimed_events.append(
                ClaimedOutboxEvent(
                    outbox_id=event.id,
                    import_id=event.import_id,
                    claim_token=claim_token,
                    attempt_count=event.attempt_count,
                )
            )

        self._session.flush()
        return claimed_events

    def mark_published(self, *, claimed_event: ClaimedOutboxEvent) -> bool:
        """Mark one successfully published event with its current claim token."""
        result = self._session.execute(
            update(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.id == claimed_event.outbox_id,
                ImportDispatchOutbox.status == "PROCESSING",
                ImportDispatchOutbox.claim_token == claimed_event.claim_token,
            )
            .values(
                status="PUBLISHED",
                published_at=func.now(),
                claim_token=None,
                last_error=None,
            )
        )
        return result.rowcount == 1

    def mark_publish_failure(
        self,
        *,
        claimed_event: ClaimedOutboxEvent,
        attempt_count: int,
        error_message: str,
        status: str,
        available_at: datetime | None,
        claimed_at: datetime | None,
    ) -> bool:
        """Persist a publisher outcome selected by the outbox service."""
        result = self._session.execute(
            update(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.id == claimed_event.outbox_id,
                ImportDispatchOutbox.status == "PROCESSING",
                ImportDispatchOutbox.claim_token == claimed_event.claim_token,
            )
            .values(
                **self._publish_failure_values(
                    attempt_count=attempt_count,
                    error_message=error_message,
                    status=status,
                    available_at=available_at,
                    claimed_at=claimed_at,
                )
            )
        )
        return result.rowcount == 1

    @staticmethod
    def _publish_failure_values(
        *,
        attempt_count: int,
        error_message: str,
        status: str,
        available_at: datetime | None,
        claimed_at: datetime | None,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "status": status,
            "attempt_count": attempt_count,
            "last_error": error_message,
            "claimed_at": claimed_at,
            "claim_token": None,
        }
        if available_at is not None:
            values["available_at"] = available_at
        return values

    def lock_stale_claims(
        self,
        *,
        batch_size: int,
        stale_before: datetime,
    ) -> list[StaleOutboxClaim]:
        """Lock a bounded stale-claim batch for recovery decisions."""
        events = self._session.scalars(
            select(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.status == "PROCESSING",
                ImportDispatchOutbox.claimed_at < stale_before,
            )
            .order_by(
                ImportDispatchOutbox.claimed_at.asc(),
                ImportDispatchOutbox.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        return [
            StaleOutboxClaim(
                outbox_id=event.id,
                import_id=event.import_id,
                claim_token=event.claim_token,
                attempt_count=event.attempt_count,
            )
            for event in events
        ]

    def requeue_stale_claim(
        self,
        *,
        stale_claim: StaleOutboxClaim,
        recovered_at: datetime,
        error_message: str,
    ) -> bool:
        """Return a locked stale publisher claim to the pending state."""
        result = self._session.execute(
            update(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.id == stale_claim.outbox_id,
                ImportDispatchOutbox.status == "PROCESSING",
                ImportDispatchOutbox.claim_token == stale_claim.claim_token,
            )
            .values(
                status="PENDING",
                available_at=recovered_at,
                claimed_at=None,
                claim_token=None,
                last_error=error_message,
            )
        )
        return result.rowcount == 1

    def mark_stale_claim_failed(
        self,
        *,
        stale_claim: StaleOutboxClaim,
        error_message: str,
    ) -> bool:
        """Persist exhaustion of a stale publisher claim."""
        result = self._session.execute(
            update(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.id == stale_claim.outbox_id,
                ImportDispatchOutbox.status == "PROCESSING",
                ImportDispatchOutbox.claim_token == stale_claim.claim_token,
            )
            .values(
                status="FAILED",
                claim_token=None,
                last_error=error_message,
            )
        )
        return result.rowcount == 1

    def create_pending_dispatch_intent(self, *, import_id: UUID, available_at: datetime) -> None:
        """Add the import-specific dispatch intent used by stale-import recovery."""
        self._session.add(
            ImportDispatchOutbox(
                import_id=import_id,
                status="PENDING",
                available_at=available_at,
            )
        )
