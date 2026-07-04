from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
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

    def create_import_job(self, import_job: ImportJob) -> UUID:
        self._session.add(import_job)
        self._session.flush()
        return import_job.id

    def create_dispatch_intent(self, dispatch_outbox: ImportDispatchOutbox) -> UUID:
        self._session.add(dispatch_outbox)
        self._session.flush()
        return dispatch_outbox.id

    def get_status_by_id(self, import_id: UUID) -> str | None:
        statement = select(ImportJob.status).where(ImportJob.id == import_id)
        return self._session.scalar(statement)

    def get_id_by_idempotency_key(self, idempotency_key: str) -> UUID | None:
        statement = select(ImportJob.id).where(ImportJob.idempotency_key == idempotency_key)
        return self._session.scalar(statement)

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
