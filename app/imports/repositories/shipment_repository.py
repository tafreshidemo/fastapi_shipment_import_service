from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.shipment import Shipment


class ShipmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bulk_insert(self, shipments: Sequence[Shipment]) -> int:
        if not shipments:
            return 0
        self._session.add_all(list(shipments))
        self._session.flush()
        return len(shipments)

    def find_existing_shipment_codes(self, shipment_codes: Iterable[str]) -> set[str]:
        codes = sorted(set(shipment_codes))
        if not codes:
            return set()
        statement = select(Shipment.shipment_code).where(Shipment.shipment_code.in_(codes))
        result = self._session.execute(statement)
        return set(result.scalars())
