from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models.import_error import ImportError as ImportErrorRow


class ImportErrorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bulk_insert(self, import_errors: Sequence[ImportErrorRow]) -> int:
        if not import_errors:
            return 0
        self._session.add_all(list(import_errors))
        self._session.flush()
        return len(import_errors)
