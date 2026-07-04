from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.domain import errors as domain_errors
from app.domain.shipment_rules import ShipmentDraft, validate_shipment_draft


def test_shipment_rules_accept_valid_draft() -> None:
    issues = validate_shipment_draft(
        ShipmentDraft(
            shipment_code="SHP-1",
            customer_name="Acme",
            origin_city="Boston",
            destination_city="Seattle",
            weight_kg=Decimal("12.5"),
            price=Decimal("99.99"),
            status="PENDING",
            delivery_date=date(2026, 6, 1),
        )
    )

    assert issues == []


def test_shipment_rules_report_all_business_rule_violations() -> None:
    issues = validate_shipment_draft(
        ShipmentDraft(
            shipment_code="SHP-1",
            customer_name="X" * 151,
            origin_city="Boston",
            destination_city="Seattle",
            weight_kg=Decimal("0"),
            price=Decimal("-1"),
            status="BROKEN",
            delivery_date=date(2026, 6, 1),
        )
    )

    assert [issue.field for issue in issues] == [
        "status",
        "customer_name",
        "weight_kg",
        "price",
    ]


def test_domain_modules_do_not_depend_on_infrastructure() -> None:
    domain_root = Path(__file__).resolve().parents[3] / "app" / "domain"
    source = "\n".join(path.read_text() for path in domain_root.glob("*.py"))

    assert "openpyxl" not in source
    assert "sqlalchemy" not in source
    assert "RabbitMQ" not in source
    assert "Path(" not in source

    assert hasattr(domain_errors, "ValidationIssue")
