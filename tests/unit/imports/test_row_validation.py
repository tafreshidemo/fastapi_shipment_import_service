from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

from app.imports.parsers.xlsx_parser import ParsedWorkbookRow, XlsxParser
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.services.row_validation import RowValidationService


def _parsed_row(row_number: int, **values: object) -> ParsedWorkbookRow:
    return ParsedWorkbookRow(
        row_number=row_number,
        values={
            "shipment_code": values.get("shipment_code"),
            "customer_name": values.get("customer_name"),
            "origin_city": values.get("origin_city"),
            "destination_city": values.get("destination_city"),
            "weight_kg": values.get("weight_kg"),
            "price": values.get("price"),
            "status": values.get("status"),
            "delivery_date": values.get("delivery_date"),
        },
    )


class FakeParser:
    def __init__(self, chunks: list[list[ParsedWorkbookRow]]) -> None:
        self._chunks = chunks

    def iter_chunks(self):
        yield from self._chunks


class FakeShipmentRepository:
    def __init__(self, existing_codes: set[str]) -> None:
        self.existing_codes = existing_codes
        self.calls: list[set[str]] = []

    def find_existing_shipment_codes(self, shipment_codes: set[str]) -> set[str]:
        self.calls.append(set(shipment_codes))
        return set(shipment_codes) & self.existing_codes


def test_row_validation_counts_rows_errors_and_duplicates_across_chunks() -> None:
    import_id = uuid4()
    parser = FakeParser(
        [
            [
                _parsed_row(
                    2,
                    shipment_code="SHP-1",
                    customer_name="Acme",
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=Decimal("12.5"),
                    price=Decimal("99.99"),
                    status="PENDING",
                    delivery_date=date(2026, 6, 1),
                ),
                _parsed_row(
                    3,
                    shipment_code="SHP-2",
                    customer_name="X" * 151,
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=Decimal("0"),
                    price=Decimal("-1"),
                    status="BROKEN",
                    delivery_date="not-a-date",
                ),
            ],
            [
                _parsed_row(
                    4,
                    shipment_code="SHP-1",
                    customer_name="Beta",
                    origin_city="Austin",
                    destination_city="Denver",
                    weight_kg=Decimal("5"),
                    price=Decimal("10"),
                    status="DELIVERED",
                    delivery_date=None,
                ),
                _parsed_row(
                    5,
                    shipment_code="SHP-3",
                    customer_name="Gamma",
                    origin_city="Miami",
                    destination_city="Dallas",
                    weight_kg=Decimal("6"),
                    price=Decimal("12"),
                    status="PENDING",
                    delivery_date=None,
                ),
            ],
        ]
    )
    shipment_repository = FakeShipmentRepository(existing_codes={"SHP-3"})
    service = RowValidationService(
        cast(XlsxParser, parser),
        cast(ShipmentRepository, shipment_repository),
    )

    result = service.validate(import_id=import_id)

    assert result.total_rows == 4
    assert result.processed_rows == 4
    assert result.success_count == 1
    assert result.failed_count == 3
    assert len(result.shipments) == 1
    assert result.shipments[0].import_id == import_id
    assert result.shipments[0].shipment_code == "SHP-1"
    assert len(result.import_errors) == 7
    assert shipment_repository.calls == [{"SHP-1", "SHP-2"}, {"SHP-3"}]
    assert {error.raw_data["shipment_code"] for error in result.import_errors} == {
        "SHP-1",
        "SHP-2",
        "SHP-3",
    }
    row_3_fields = {error.field for error in result.import_errors if error.row_number == 3}
    assert row_3_fields == {
        "customer_name",
        "weight_kg",
        "price",
        "status",
        "delivery_date",
    }
    assert any(error.raw_data["customer_name"] == "X" * 151 for error in result.import_errors)


def test_row_validation_preserves_sanitized_raw_data_and_multiple_errors() -> None:
    parser = FakeParser(
        [
            [
                _parsed_row(
                    2,
                    shipment_code="SHP-4",
                    customer_name="X" * 151,
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=Decimal("0"),
                    price=Decimal("-0.01"),
                    status="INVALID",
                    delivery_date=None,
                )
            ]
        ]
    )
    shipment_repository = FakeShipmentRepository(existing_codes=set())
    service = RowValidationService(
        cast(XlsxParser, parser),
        cast(ShipmentRepository, shipment_repository),
    )

    result = service.validate(import_id=uuid4())

    assert result.total_rows == 1
    assert result.success_count == 0
    assert result.failed_count == 1
    assert len(result.import_errors) == 4
    assert result.import_errors[0].raw_data["origin_city"] == "Boston"
    assert result.import_errors[0].raw_data["shipment_code"] == "SHP-4"


def test_row_validation_converts_raw_data_to_jsonb_safe_primitives() -> None:
    original_delivery_date = date(2026, 6, 1)
    original_origin_city = datetime(2026, 6, 2, 9, 30, 15)
    parser = FakeParser(
        [
            [
                _parsed_row(
                    2,
                    shipment_code="SHP-5",
                    customer_name="Acme",
                    origin_city=original_origin_city,
                    destination_city="Seattle",
                    weight_kg=Decimal("0"),
                    price=Decimal("10.25"),
                    status="PENDING",
                    delivery_date=original_delivery_date,
                )
            ]
        ]
    )
    shipment_repository = FakeShipmentRepository(existing_codes=set())
    service = RowValidationService(
        cast(XlsxParser, parser),
        cast(ShipmentRepository, shipment_repository),
    )

    result = service.validate(import_id=uuid4())

    assert result.failed_count == 1
    assert result.import_errors
    raw_data = result.import_errors[0].raw_data
    assert raw_data["weight_kg"] == "0"
    assert raw_data["price"] == "10.25"
    assert raw_data["delivery_date"] == "2026-06-01"
    assert raw_data["origin_city"] == "2026-06-02T09:30:15"
    assert isinstance(raw_data["weight_kg"], str)
    assert isinstance(raw_data["price"], str)
    assert isinstance(raw_data["delivery_date"], str)
    assert isinstance(raw_data["origin_city"], str)
