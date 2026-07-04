from __future__ import annotations

REQUIRED_WORKBOOK_HEADERS = (
    "shipment_code",
    "customer_name",
    "origin_city",
    "destination_city",
    "weight_kg",
    "price",
    "status",
)
OPTIONAL_WORKBOOK_HEADERS = ("delivery_date",)
WORKBOOK_ROW_FIELDS = (*REQUIRED_WORKBOOK_HEADERS, *OPTIONAL_WORKBOOK_HEADERS)
