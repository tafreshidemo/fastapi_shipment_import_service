from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from app.common.pagination import PageRequest, build_pagination_metadata
from app.shipments.repositories.shipment_query_repository import (
    ShipmentQueryFilters,
    ShipmentQueryRepository,
)

_SHIPMENT_STATUSES = frozenset({"PENDING", "IN_TRANSIT", "DELIVERED", "CANCELED"})


class InvalidShipmentFilterError(ValueError):
    """Raised when shipment list filters do not match the public contract."""


class ListShipmentsService:
    """Read paginated shipments with database-backed filters."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_shipments(
        self,
        *,
        page_request: PageRequest,
        status: str | None,
        origin_city: str | None,
        destination_city: str | None,
        customer_name: str | None,
        created_from: str | None,
        created_to: str | None,
    ) -> dict[str, object]:
        filters = _build_filters(
            status=status,
            origin_city=origin_city,
            destination_city=destination_city,
            customer_name=customer_name,
            created_from=created_from,
            created_to=created_to,
        )
        with self._session_factory() as session:
            result = ShipmentQueryRepository(session).list_page(
                filters=filters,
                limit=page_request.page_size,
                offset=page_request.offset,
            )

        return {
            "items": [asdict(item) for item in result.items],
            "pagination": build_pagination_metadata(
                request=page_request,
                total_items=result.total_items,
            ).as_dict(),
        }


def _build_filters(
    *,
    status: str | None,
    origin_city: str | None,
    destination_city: str | None,
    customer_name: str | None,
    created_from: str | None,
    created_to: str | None,
) -> ShipmentQueryFilters:
    normalized_status = _normalize_status(status)
    parsed_created_from = _parse_date_filter(created_from, "created_from")
    parsed_created_to = _parse_date_filter(created_to, "created_to")
    if (
        parsed_created_from is not None
        and parsed_created_to is not None
        and parsed_created_from > parsed_created_to
    ):
        raise InvalidShipmentFilterError("created_from must be on or before created_to.")

    return ShipmentQueryFilters(
        status=normalized_status,
        origin_city=_normalize_text_filter(origin_city),
        destination_city=_normalize_text_filter(destination_city),
        customer_name=_normalize_text_filter(customer_name),
        created_from=parsed_created_from,
        created_to=parsed_created_to,
    )


def _normalize_status(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized not in _SHIPMENT_STATUSES:
        allowed = ", ".join(sorted(_SHIPMENT_STATUSES))
        raise InvalidShipmentFilterError(f"status must be one of: {allowed}.")
    return normalized


def _normalize_text_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise InvalidShipmentFilterError("text filters must not be blank.")
    return normalized


def _parse_date_filter(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidShipmentFilterError(f"{name} must use YYYY-MM-DD.") from exc
