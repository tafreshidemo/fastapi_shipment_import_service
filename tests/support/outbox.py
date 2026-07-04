from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox


def add_dispatch_event(
    session: Session,
    *,
    import_id: UUID,
    status: str = "PENDING",
    attempt_count: int = 0,
    available_at: datetime | None = None,
    claimed_at: datetime | None = None,
    claim_token: UUID | None = None,
) -> ImportDispatchOutbox:
    """Persist one import-dispatch event with explicit state for integration tests."""
    event = ImportDispatchOutbox(
        import_id=import_id,
        status=status,
        attempt_count=attempt_count,
        available_at=available_at or datetime.now(timezone.utc),
        claimed_at=claimed_at,
        claim_token=claim_token,
    )
    session.add(event)
    session.flush()
    return event
