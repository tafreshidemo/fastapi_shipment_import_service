from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob
from app.domain.import_states import ImportStatus


@dataclass(frozen=True, slots=True)
class ClaimedImport:
    """Committed worker ownership details for one import."""

    import_id: UUID
    stored_file_path: Path
    processing_token: UUID
    attempt_count: int
    max_attempts: int


class ImportClaimRepository:
    """Performs the short, database-backed import ownership claim."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_pending_import(
        self,
        *,
        import_id: UUID,
        processing_token: UUID,
        worker_id: str,
    ) -> ClaimedImport | None:
        """Claim one pending import without waiting on another worker's lock."""
        locked_import_id = self._session.scalar(
            select(ImportJob.id)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PENDING,
                ImportJob.attempt_count < ImportJob.max_attempts,
            )
            .with_for_update(skip_locked=True)
        )
        if locked_import_id is None:
            return None

        claimed = self._session.execute(
            update(ImportJob)
            .where(
                ImportJob.id == import_id,
                ImportJob.status == ImportStatus.PENDING,
                ImportJob.attempt_count < ImportJob.max_attempts,
            )
            .values(
                status=ImportStatus.PROCESSING,
                processing_token=processing_token,
                locked_by_worker=worker_id,
                attempt_count=ImportJob.attempt_count + 1,
                started_at=func.now(),
                last_heartbeat_at=func.now(),
            )
            .returning(
                ImportJob.id,
                ImportJob.stored_file_path,
                ImportJob.attempt_count,
                ImportJob.max_attempts,
            )
        ).one_or_none()
        if claimed is None:
            return None

        claimed_id, stored_file_path, attempt_count, max_attempts = claimed
        return ClaimedImport(
            import_id=claimed_id,
            stored_file_path=Path(stored_file_path),
            processing_token=processing_token,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )
