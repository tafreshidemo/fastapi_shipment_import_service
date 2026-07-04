from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.shipment import Shipment


@dataclass(frozen=True, slots=True)
class ShipmentQueryFilters:
    status: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    customer_name: str | None = None
    created_from: date | None = None
    created_to: date | None = None


@dataclass(frozen=True, slots=True)
class ShipmentListItem:
    shipment_id: UUID
    import_id: UUID
    shipment_code: str
    customer_name: str
    origin_city: str
    destination_city: str
    weight_kg: str
    price: str
    status: str
    delivery_date: date | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ShipmentPage:
    items: tuple[ShipmentListItem, ...]
    total_items: int


class ShipmentQueryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_page(
        self,
        *,
        filters: ShipmentQueryFilters,
        limit: int,
        offset: int,
    ) -> ShipmentPage:
        criteria = _filters_to_criteria(filters)
        total_items = self._session.scalar(
            select(func.count()).select_from(Shipment).where(*criteria)
        )
        shipments = self._session.scalars(
            select(Shipment)
            .where(*criteria)
            .order_by(Shipment.created_at.desc(), Shipment.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return ShipmentPage(
            items=tuple(_to_list_item(shipment) for shipment in shipments),
            total_items=total_items or 0,
        )


def _filters_to_criteria(filters: ShipmentQueryFilters) -> list[object]:
    criteria: list[object] = []
    if filters.status is not None:
        criteria.append(Shipment.status == filters.status)
    if filters.origin_city is not None:
        criteria.append(_contains(Shipment.origin_city, filters.origin_city))
    if filters.destination_city is not None:
        criteria.append(_contains(Shipment.destination_city, filters.destination_city))
    if filters.customer_name is not None:
        criteria.append(_contains(Shipment.customer_name, filters.customer_name))
    if filters.created_from is not None:
        criteria.append(Shipment.created_at >= _day_start(filters.created_from))
    if filters.created_to is not None:
        criteria.append(Shipment.created_at < _day_start(filters.created_to + timedelta(days=1)))
    return criteria


def _contains(column, value: str):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _to_list_item(shipment: Shipment) -> ShipmentListItem:
    return ShipmentListItem(
        shipment_id=shipment.id,
        import_id=shipment.import_id,
        shipment_code=shipment.shipment_code,
        customer_name=shipment.customer_name,
        origin_city=shipment.origin_city,
        destination_city=shipment.destination_city,
        weight_kg=_decimal_text(shipment.weight_kg),
        price=_decimal_text(shipment.price),
        status=shipment.status,
        delivery_date=shipment.delivery_date,
        created_at=shipment.created_at,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
