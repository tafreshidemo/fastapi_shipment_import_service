from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob


class ImportJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, import_job: ImportJob) -> UUID:
        self._session.add(import_job)
        self._session.flush()
        return import_job.id

    def get_status_by_id(self, import_id: UUID) -> str | None:
        statement = select(ImportJob.status).where(ImportJob.id == import_id)
        return self._session.scalar(statement)

    def get_id_by_idempotency_key(self, idempotency_key: str) -> UUID | None:
        statement = select(ImportJob.id).where(ImportJob.idempotency_key == idempotency_key)
        return self._session.scalar(statement)
