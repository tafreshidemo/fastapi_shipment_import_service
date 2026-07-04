from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.domain.import_states import ImportStatus
from app.imports.dto import ImportStatusReadModel


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

    def exists(self, import_id: UUID) -> bool:
        statement = select(exists().where(ImportJob.id == import_id))
        return bool(self._session.scalar(statement))

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

    def fail_pending_import_after_dispatch_exhausted(self, *, import_id: UUID, reason: str) -> bool:
        """Fail an import only while dispatch remains the reason processing never started."""
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PENDING,
            )
            .values(
                status=ImportStatus.FAILED,
                failure_reason=reason,
                last_failure_reason=reason,
                finished_at=func.now(),
                processing_token=None,
                locked_by_worker=None,
            )
        )
        return result.rowcount == 1

    def get_status_read_model(self, import_id: UUID) -> ImportStatusReadModel | None:
        row = self._session.execute(
            select(
                ImportJob.id.label("import_id"),
                ImportJob.status,
                ImportJob.total_rows,
                ImportJob.processed_rows,
                ImportJob.success_count,
                ImportJob.failed_count,
                ImportJob.created_at,
                ImportJob.started_at,
                ImportJob.finished_at,
                ImportJob.last_failure_reason,
                ImportJob.failure_reason,
            ).where(ImportJob.id == import_id)
        ).one_or_none()
        if row is None:
            return None
        return ImportStatusReadModel(
            import_id=row.import_id,
            status=row.status,
            total_rows=row.total_rows,
            processed_rows=row.processed_rows,
            success_count=row.success_count,
            failed_count=row.failed_count,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            last_failure_reason=row.last_failure_reason,
            failure_reason=row.failure_reason,
        )
