from __future__ import annotations

from collections.abc import Sequence
from sqlalchemy.orm import Session

from app.db.models.import_error import ImportError as ImportErrorRow
from app.imports.jsonb import jsonb_safe


class ImportErrorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bulk_insert(self, import_errors: Sequence[ImportErrorRow]) -> int:
        if not import_errors:
            return 0
        rows = list(import_errors)
        for import_error in rows:
            import_error.raw_data = jsonb_safe(import_error.raw_data)
        self._session.add_all(rows)
        self._session.flush()
        return len(rows)

