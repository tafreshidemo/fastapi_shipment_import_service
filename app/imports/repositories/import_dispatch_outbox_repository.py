from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox


class ImportDispatchOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, dispatch_outbox: ImportDispatchOutbox) -> UUID:
        self._session.add(dispatch_outbox)
        self._session.flush()
        return dispatch_outbox.id
