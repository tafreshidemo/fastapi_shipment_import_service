from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.imports.repositories.import_repository import ImportRepository


class ImportNotFoundError(LookupError):
    """Raised when an import identifier has no persisted job."""


class GetImportStatusService:
    """Read current import progress from committed PostgreSQL state."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_status(self, import_id: UUID) -> dict[str, object]:
        with self._session_factory() as session:
            record = ImportRepository(session).get_status_read_model(import_id)

        if record is None:
            raise ImportNotFoundError

        payload = asdict(record)
        if record.status == "COMPLETED" or (
            record.status != "FAILED" and record.last_failure_reason is None
        ):
            payload.pop("last_failure_reason", None)
        if record.status != "FAILED":
            payload.pop("failure_reason", None)
        return payload
