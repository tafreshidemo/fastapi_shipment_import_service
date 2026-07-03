from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob


@dataclass(frozen=True, slots=True)
class ImportSnapshot:
    import_id: UUID
    status: str
    created_at: datetime
    idempotency_fingerprint: str


class ImportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_snapshot_by_idempotency_key(self, idempotency_key: str) -> ImportSnapshot | None:
        statement = select(
            ImportJob.id,
            ImportJob.status,
            ImportJob.created_at,
            ImportJob.idempotency_fingerprint,
        ).where(ImportJob.idempotency_key == idempotency_key)
        row = self._session.execute(statement).one_or_none()
        if row is None:
            return None
        import_id, status, created_at, idempotency_fingerprint = row
        return ImportSnapshot(
            import_id=import_id,
            status=status,
            created_at=created_at,
            idempotency_fingerprint=idempotency_fingerprint,
        )
