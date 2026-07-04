from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models.import_error import ImportError as ImportErrorRow
from app.imports.dto import ImportErrorReadModel
from app.imports.jsonb import jsonb_safe


@dataclass(frozen=True, slots=True)
class ImportErrorPage:
    items: tuple[ImportErrorReadModel, ...]
    total_items: int


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

    def delete_by_import_id(self, import_id: UUID) -> int:
        result = self._session.execute(
            delete(ImportErrorRow).where(ImportErrorRow.import_id == import_id)
        )
        return result.rowcount or 0

    def list_page(self, *, import_id: UUID, limit: int, offset: int) -> ImportErrorPage:
        total_items = self._session.scalar(
            select(func.count())
            .select_from(ImportErrorRow)
            .where(ImportErrorRow.import_id == import_id)
        )
        rows = self._session.execute(
            select(
                ImportErrorRow.row_number,
                ImportErrorRow.field,
                ImportErrorRow.error,
            )
            .where(ImportErrorRow.import_id == import_id)
            .order_by(ImportErrorRow.row_number.asc(), ImportErrorRow.id.asc())
            .limit(limit)
            .offset(offset)
        ).all()
        return ImportErrorPage(
            items=tuple(
                ImportErrorReadModel(
                    row_number=row.row_number,
                    field=row.field,
                    error=row.error,
                )
                for row in rows
            ),
            total_items=total_items or 0,
        )
