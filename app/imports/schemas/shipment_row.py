from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


class ShipmentRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    shipment_code: str
    customer_name: str
    origin_city: str
    destination_city: str
    weight_kg: Decimal
    price: Decimal
    status: str
    delivery_date: date | None = None

    @field_validator(
        "shipment_code",
        "customer_name",
        "origin_city",
        "destination_city",
        "status",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("delivery_date", mode="before")
    @classmethod
    def normalize_delivery_date(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value
