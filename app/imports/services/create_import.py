from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.imports.dto import ImportCreatedResult
from app.imports.repositories.import_repository import ImportRepository


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused for a different upload."""


class DatabaseWriteError(Exception):
    """Raised when the import or dispatch transaction cannot be committed."""


@dataclass(frozen=True, slots=True)
class ImportCreateOutcome:
    result: ImportCreatedResult
    created: bool


class CreateImportService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def create_import(
        self,
        *,
        original_file_name: str,
        stored_file_path: str,
        file_size_bytes: int,
        content_type: str | None,
        idempotency_key: str | None,
        idempotency_fingerprint: str,
    ) -> ImportCreateOutcome:
        with self._session_factory() as session:
            import_repository = ImportRepository(session)

            if idempotency_key is not None:
                existing = import_repository.get_snapshot_by_idempotency_key(idempotency_key)
                if existing is not None:
                    if existing.idempotency_fingerprint != idempotency_fingerprint:
                        raise IdempotencyConflictError(
                            "Idempotency key already exists for a different upload."
                        )
                    return ImportCreateOutcome(
                        result=ImportCreatedResult(
                            import_id=existing.import_id,
                            status=existing.status,
                            created_at=existing.created_at,
                        ),
                        created=False,
                    )

            import_job = ImportJob(
                status="PENDING",
                original_file_name=original_file_name,
                stored_file_path=stored_file_path,
                file_size_bytes=file_size_bytes,
                content_type=content_type,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=idempotency_fingerprint,
                total_rows=0,
                processed_rows=0,
                success_count=0,
                failed_count=0,
                attempt_count=0,
                max_attempts=self._settings.import_max_attempts,
            )

            try:
                import_repository.create_import_job(import_job)
                import_repository.create_dispatch_intent(
                    ImportDispatchOutbox(import_id=import_job.id)
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if idempotency_key is None:
                    raise DatabaseWriteError("Failed to persist import records.") from exc

                existing = import_repository.get_snapshot_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise DatabaseWriteError("Failed to persist import records.") from exc
                if existing.idempotency_fingerprint != idempotency_fingerprint:
                    raise IdempotencyConflictError(
                        "Idempotency key already exists for a different upload."
                    ) from exc
                return ImportCreateOutcome(
                    result=ImportCreatedResult(
                        import_id=existing.import_id,
                        status=existing.status,
                        created_at=existing.created_at,
                    ),
                    created=False,
                )
            except SQLAlchemyError as exc:
                session.rollback()
                raise DatabaseWriteError("Failed to persist import records.") from exc

            session.refresh(import_job)
            return ImportCreateOutcome(
                result=ImportCreatedResult(
                    import_id=import_job.id,
                    status=import_job.status,
                    created_at=import_job.created_at,
                ),
                created=True,
            )
