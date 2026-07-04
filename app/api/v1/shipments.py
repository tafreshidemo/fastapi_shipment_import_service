from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.common.pagination import InvalidPaginationError, parse_page_request
from app.core.settings import Settings, get_settings
from app.db.session import get_session_factory
from app.shipments.services.list_shipments import (
    InvalidShipmentFilterError,
    ListShipmentsService,
)

router = APIRouter(tags=["shipments"])
SETTINGS_DEPENDENCY = Depends(get_settings)
SESSION_FACTORY_DEPENDENCY = Depends(get_session_factory)


@router.get("/shipments")
def list_shipments(
    page: str | None = None,
    page_size: str | None = None,
    status: str | None = None,
    origin_city: str | None = None,
    destination_city: str | None = None,
    customer_name: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    settings: Settings = SETTINGS_DEPENDENCY,
    session_factory: sessionmaker[Session] = SESSION_FACTORY_DEPENDENCY,
) -> dict[str, object]:
    try:
        page_request = parse_page_request(
            page=page,
            page_size=page_size,
            default_page_size=settings.default_page_size,
            max_page_size=settings.max_page_size,
        )
    except InvalidPaginationError as exc:
        raise ApiError("INVALID_PAGINATION", str(exc)) from exc

    try:
        return ListShipmentsService(session_factory).list_shipments(
            page_request=page_request,
            status=status,
            origin_city=origin_city,
            destination_city=destination_city,
            customer_name=customer_name,
            created_from=created_from,
            created_to=created_to,
        )
    except InvalidShipmentFilterError as exc:
        raise ApiError("INVALID_FILTER", str(exc)) from exc
