from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob
from app.domain.import_states import ImportStatus


class ImportProgressRepository:
    """Writes token-checked progress and terminal import state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def has_current_ownership(self, *, import_id: UUID, processing_token: UUID) -> bool:
        statement = select(ImportJob.id).where(
            ImportJob.id == import_id,
            ImportJob.status == ImportStatus.PROCESSING,
            ImportJob.processing_token == processing_token,
        )
        return self._session.scalar(statement) is not None

    def reset_for_reprocessing(self, *, import_id: UUID, processing_token: UUID) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(
                total_rows=0,
                processed_rows=0,
                success_count=0,
                failed_count=0,
                finished_at=None,
            )
        )
        return result.rowcount == 1

    def record_chunk_counts(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        total_rows: int,
        success_count: int,
        failed_count: int,
    ) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(
                total_rows=ImportJob.total_rows + total_rows,
                processed_rows=ImportJob.processed_rows + total_rows,
                success_count=ImportJob.success_count + success_count,
                failed_count=ImportJob.failed_count + failed_count,
            )
        )
        return result.rowcount == 1

    def heartbeat(self, *, import_id: UUID, processing_token: UUID) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(last_heartbeat_at=func.now())
        )
        return result.rowcount == 1

    def complete(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        total_rows: int,
        success_count: int,
        failed_count: int,
    ) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(
                status=ImportStatus.COMPLETED,
                total_rows=total_rows,
                processed_rows=total_rows,
                success_count=success_count,
                failed_count=failed_count,
                finished_at=func.now(),
                last_heartbeat_at=func.now(),
                processing_token=None,
                locked_by_worker=None,
            )
        )
        return result.rowcount == 1

    def fail(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        reason: str,
    ) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
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

    def requeue_for_retry(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        reason: str,
    ) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(
                status=ImportStatus.PENDING,
                failure_reason=None,
                last_failure_reason=reason,
                processing_token=None,
                locked_by_worker=None,
            )
        )
        return result.rowcount == 1
