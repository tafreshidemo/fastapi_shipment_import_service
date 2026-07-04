from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob
from app.domain.import_states import ImportStatus


class ImportProgressRepository:
    """Write token-checked import progress without owning transactions.

    Refinement made: ProcessImportService owns chunk transactions; this
    repository only persists progress and terminal state transitions.
    """

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
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={
                "total_rows": 0,
                "processed_rows": 0,
                "success_count": 0,
                "failed_count": 0,
                "finished_at": None,
            },
        )

    def record_chunk_counts(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        total_rows: int,
        success_count: int,
        failed_count: int,
    ) -> bool:
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={
                "total_rows": ImportJob.total_rows + total_rows,
                "processed_rows": ImportJob.processed_rows + total_rows,
                "success_count": ImportJob.success_count + success_count,
                "failed_count": ImportJob.failed_count + failed_count,
            },
        )

    def heartbeat(self, *, import_id: UUID, processing_token: UUID) -> bool:
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={"last_heartbeat_at": func.now()},
        )

    def complete(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        total_rows: int,
        success_count: int,
        failed_count: int,
    ) -> bool:
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={
                "status": ImportStatus.COMPLETED,
                "total_rows": total_rows,
                "processed_rows": total_rows,
                "success_count": success_count,
                "failed_count": failed_count,
                "finished_at": func.now(),
                "last_heartbeat_at": func.now(),
                "processing_token": None,
                "locked_by_worker": None,
            },
        )

    def fail(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        reason: str,
    ) -> bool:
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={
                "status": ImportStatus.FAILED,
                "failure_reason": reason,
                "last_failure_reason": reason,
                "finished_at": func.now(),
                "processing_token": None,
                "locked_by_worker": None,
            },
        )

    def requeue_for_retry(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        reason: str,
    ) -> bool:
        return self._update_current_processing_import(
            import_id=import_id,
            processing_token=processing_token,
            values={
                "status": ImportStatus.PENDING,
                "failure_reason": None,
                "last_failure_reason": reason,
                "processing_token": None,
                "locked_by_worker": None,
            },
        )

    def _update_current_processing_import(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        values: dict[str, object],
    ) -> bool:
        result = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PROCESSING,
                ImportJob.processing_token == processing_token,
            )
            .values(**values)
        )
        return result.rowcount == 1
