from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.errors import ValidationIssue

# Refinement made: Step 4 framework-independent shipment rules were
# extracted from row validation after the parser contract stabilized.
ALLOWED_SHIPMENT_STATUSES = frozenset(
    {
        "PENDING",
        "IN_TRANSIT",
        "DELIVERED",
        "CANCELED",
    }
)
REQUIRED_SHIPMENT_FIELDS = (
    "shipment_code",
    "customer_name",
    "origin_city",
    "destination_city",
    "weight_kg",
    "price",
    "status",
)
CUSTOMER_NAME_MAX_LENGTH = 150


@dataclass(frozen=True, slots=True)
class ShipmentDraft:
    shipment_code: str
    customer_name: str
    origin_city: str
    destination_city: str
    weight_kg: Decimal
    price: Decimal
    status: str
    delivery_date: date | None


@dataclass(frozen=True, slots=True)
class PartialShipmentDraft:
    shipment_code: str | None = None
    customer_name: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    weight_kg: Decimal | None = None
    price: Decimal | None = None
    status: str | None = None
    delivery_date: date | None = None


def validate_shipment_draft(shipment: ShipmentDraft) -> list[ValidationIssue]:
    return validate_partial_shipment_draft(
        PartialShipmentDraft(
            shipment_code=shipment.shipment_code,
            customer_name=shipment.customer_name,
            origin_city=shipment.origin_city,
            destination_city=shipment.destination_city,
            weight_kg=shipment.weight_kg,
            price=shipment.price,
            status=shipment.status,
            delivery_date=shipment.delivery_date,
        )
    )


def validate_partial_shipment_draft(shipment: PartialShipmentDraft) -> list[ValidationIssue]:
    """Validate business rules for values that were parsed successfully."""
    issues: list[ValidationIssue] = []

    if shipment.status is not None and shipment.status not in ALLOWED_SHIPMENT_STATUSES:
        issues.append(
            ValidationIssue(
                field="status",
                error=(
                    "Status must be one of PENDING, IN_TRANSIT, DELIVERED, or CANCELED."
                ),
            )
        )

    if (
        shipment.customer_name is not None
        and len(shipment.customer_name) > CUSTOMER_NAME_MAX_LENGTH
    ):
        issues.append(
            ValidationIssue(
                field="customer_name",
                error="Customer name must be 150 characters or fewer.",
            )
        )

    if shipment.weight_kg is not None and shipment.weight_kg <= 0:
        issues.append(
            ValidationIssue(
                field="weight_kg",
                error="Weight must be greater than 0.",
            )
        )

    if shipment.price is not None and shipment.price < 0:
        issues.append(
            ValidationIssue(
                field="price",
                error="Price must be greater than or equal to 0.",
            )
        )

    if shipment.delivery_date is not None and not isinstance(shipment.delivery_date, date):
        issues.append(
            ValidationIssue(
                field="delivery_date",
                error="Delivery date must be a valid date when present.",
            )
        )

    return issues
