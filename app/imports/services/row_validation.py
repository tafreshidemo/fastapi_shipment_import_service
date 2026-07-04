from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.db.models.import_error import ImportError as ImportErrorRow
from app.db.models.shipment import Shipment
from app.domain.errors import ValidationIssue
from app.domain.shipment_rules import (
    PartialShipmentDraft,
    ShipmentDraft,
    validate_partial_shipment_draft,
    validate_shipment_draft,
)
from app.imports.jsonb import jsonb_safe
from app.imports.parsers.workbook_contract import REQUIRED_WORKBOOK_HEADERS
from app.imports.parsers.xlsx_parser import ParsedWorkbookRow, XlsxParser
from app.imports.repositories.shipment_repository import ShipmentRepository
from app.imports.schemas.shipment_row import ShipmentRow
from app.imports.services.duplicate_tracker import DuplicateTracker

_STRING_ADAPTER = TypeAdapter(str)
_DECIMAL_ADAPTER = TypeAdapter(Decimal)
_DATE_ADAPTER = TypeAdapter(date)


@dataclass(frozen=True, slots=True)
class ValidatedShipmentRow:
    shipment: Shipment
    row_number: int
    raw_data: dict[str, object | None]


@dataclass(frozen=True, slots=True)
class ValidationChunkResult:
    total_rows: int
    processed_rows: int
    success_count: int
    failed_count: int
    shipments: list[Shipment]
    import_errors: list[ImportErrorRow]
    valid_rows: list[ValidatedShipmentRow]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    total_rows: int
    processed_rows: int
    success_count: int
    failed_count: int
    shipments: list[Shipment]
    import_errors: list[ImportErrorRow]


@dataclass(slots=True)
class ValidationAccumulator:
    total_rows: int = 0
    success_count: int = 0
    failed_count: int = 0
    shipments: list[Shipment] = field(default_factory=list)
    import_errors: list[ImportErrorRow] = field(default_factory=list)

    def add_chunk(self, chunk_result: ValidationChunkResult) -> None:
        self.total_rows += chunk_result.total_rows
        self.success_count += chunk_result.success_count
        self.failed_count += chunk_result.failed_count
        self.shipments.extend(chunk_result.shipments)
        self.import_errors.extend(chunk_result.import_errors)

    def to_result(self) -> ValidationResult:
        return ValidationResult(
            total_rows=self.total_rows,
            processed_rows=self.success_count + self.failed_count,
            success_count=self.success_count,
            failed_count=self.failed_count,
            shipments=self.shipments,
            import_errors=self.import_errors,
        )


@dataclass(frozen=True, slots=True)
class _PreparedRow:
    row: ParsedWorkbookRow
    issues: tuple[ValidationIssue, ...]
    parsed_row: ShipmentRow | None
    shipment_code: str | None


class RowValidationService:
    def __init__(
        self,
        parser: XlsxParser,
        shipment_repository: ShipmentRepository,
    ) -> None:
        self._parser = parser
        self._shipment_repository = shipment_repository

    def iter_validated_chunks(self, *, import_id: UUID) -> Iterator[ValidationChunkResult]:
        tracker = DuplicateTracker()
        for chunk in self._parser.iter_chunks():
            yield self.validate_chunk(chunk, tracker, import_id=import_id)

    def validate(self, *, import_id: UUID) -> ValidationResult:
        """Collect all chunks for focused Step 4 tests and small callers only.

        Worker processing must use iter_validated_chunks() so Step 5 can persist each
        chunk without holding the complete workbook result in memory.
        """
        accumulator = ValidationAccumulator()
        for chunk_result in self.iter_validated_chunks(import_id=import_id):
            accumulator.add_chunk(chunk_result)
        return accumulator.to_result()

    def validate_chunk(
        self,
        chunk: list[ParsedWorkbookRow],
        tracker: DuplicateTracker,
        *,
        import_id: UUID,
    ) -> ValidationChunkResult:
        """Validate one parsed chunk without accumulating the workbook."""
        prepared_rows = [self._prepare_row(row) for row in chunk]
        chunk_codes = {
            prepared_row.shipment_code
            for prepared_row in prepared_rows
            if prepared_row.shipment_code is not None
        }
        existing_db_codes = self._shipment_repository.find_existing_shipment_codes(
            tracker.unseen_codes(chunk_codes)
        )

        shipments: list[Shipment] = []
        import_errors: list[ImportErrorRow] = []
        valid_rows: list[ValidatedShipmentRow] = []
        chunk_seen_codes: set[str] = set()
        failed_count = 0

        for prepared_row in prepared_rows:
            row_issues = list(prepared_row.issues)
            shipment_code = prepared_row.shipment_code
            if shipment_code is not None:
                if (
                    shipment_code in tracker.seen_shipment_codes
                    or shipment_code in chunk_seen_codes
                ):
                    row_issues.append(
                        ValidationIssue(
                            field="shipment_code",
                            error="Shipment code must be unique within the import file.",
                        )
                    )
                elif shipment_code in existing_db_codes:
                    row_issues.append(
                        ValidationIssue(
                            field="shipment_code",
                            error="Shipment code already exists in the database.",
                        )
                    )
                chunk_seen_codes.add(shipment_code)

            if row_issues:
                failed_count += 1
                import_errors.extend(
                    self._build_import_errors(
                        import_id=import_id,
                        row_number=prepared_row.row.row_number,
                        raw_data=prepared_row.row.values,
                        issues=row_issues,
                    )
                )
                continue

            assert prepared_row.parsed_row is not None
            shipment = Shipment(
                id=uuid4(),
                import_id=import_id,
                shipment_code=prepared_row.parsed_row.shipment_code,
                customer_name=prepared_row.parsed_row.customer_name,
                origin_city=prepared_row.parsed_row.origin_city,
                destination_city=prepared_row.parsed_row.destination_city,
                weight_kg=prepared_row.parsed_row.weight_kg,
                price=prepared_row.parsed_row.price,
                status=prepared_row.parsed_row.status,
                delivery_date=prepared_row.parsed_row.delivery_date,
            )
            shipments.append(shipment)
            valid_rows.append(
                ValidatedShipmentRow(
                    shipment=shipment,
                    row_number=prepared_row.row.row_number,
                    raw_data=dict(prepared_row.row.values),
                )
            )

        tracker.remember_codes(chunk_seen_codes)
        success_count = len(shipments)
        return ValidationChunkResult(
            total_rows=len(chunk),
            processed_rows=success_count + failed_count,
            success_count=success_count,
            failed_count=failed_count,
            shipments=shipments,
            import_errors=import_errors,
            valid_rows=valid_rows,
        )

    def reclassify_database_duplicates(
        self,
        *,
        import_id: UUID,
        chunk_result: ValidationChunkResult,
    ) -> ValidationChunkResult:
        """Turn concurrent global-code conflicts into row-level validation errors."""
        existing_codes = self._shipment_repository.find_existing_shipment_codes(
            {valid_row.shipment.shipment_code for valid_row in chunk_result.valid_rows}
        )
        if not existing_codes:
            return chunk_result

        retained_rows: list[ValidatedShipmentRow] = []
        import_errors = list(chunk_result.import_errors)
        for valid_row in chunk_result.valid_rows:
            if valid_row.shipment.shipment_code not in existing_codes:
                retained_rows.append(valid_row)
                continue
            import_errors.extend(
                self._build_import_errors(
                    import_id=import_id,
                    row_number=valid_row.row_number,
                    raw_data=valid_row.raw_data,
                    issues=[
                        ValidationIssue(
                            field="shipment_code",
                            error="Shipment code already exists in the database.",
                        )
                    ],
                )
            )

        success_count = len(retained_rows)
        failed_count = chunk_result.failed_count + (
            len(chunk_result.valid_rows) - success_count
        )
        return ValidationChunkResult(
            total_rows=chunk_result.total_rows,
            processed_rows=chunk_result.processed_rows,
            success_count=success_count,
            failed_count=failed_count,
            shipments=[valid_row.shipment for valid_row in retained_rows],
            import_errors=import_errors,
            valid_rows=retained_rows,
        )

    def _prepare_row(self, row: ParsedWorkbookRow) -> _PreparedRow:
        parsed_errors, parsed_values, parsed_row = self._parse_row(row)
        shipment_code = (
            parsed_row.shipment_code
            if parsed_row is not None
            else self._parsed_shipment_code(parsed_values)
        )
        return _PreparedRow(
            row=row,
            issues=tuple(parsed_errors),
            parsed_row=parsed_row,
            shipment_code=shipment_code,
        )

    def _parse_row(
        self,
        row: ParsedWorkbookRow,
    ) -> tuple[list[ValidationIssue], dict[str, object], ShipmentRow | None]:
        issues: list[ValidationIssue] = []
        parsed_values: dict[str, object] = {}

        for field_name in REQUIRED_WORKBOOK_HEADERS:
            value = row.values.get(field_name)
            if value is None:
                issues.append(ValidationIssue(field=field_name, error="Field required"))
                continue
            adapter = _DECIMAL_ADAPTER if field_name in {"weight_kg", "price"} else _STRING_ADAPTER
            parsed = self._parse_field(adapter, field_name, value)
            if isinstance(parsed, ValidationIssue):
                issues.append(parsed)
                continue
            parsed_values[field_name] = parsed

        delivery_date_value = row.values.get("delivery_date")
        if delivery_date_value is None:
            parsed_values["delivery_date"] = None
        else:
            parsed = self._parse_field(_DATE_ADAPTER, "delivery_date", delivery_date_value)
            if isinstance(parsed, ValidationIssue):
                issues.append(parsed)
                parsed_values["delivery_date"] = None
            else:
                parsed_values["delivery_date"] = parsed

        required_parsed = all(
            field_name in parsed_values for field_name in REQUIRED_WORKBOOK_HEADERS
        )
        if not required_parsed:
            issues.extend(self._validate_partial_row_rules(parsed_values))
            return issues, parsed_values, None

        shipment_row = ShipmentRow.model_construct(**parsed_values)
        issues.extend(self._validate_row_rules(shipment_row))
        return issues, parsed_values, shipment_row

    def _parse_field(
        self,
        adapter: TypeAdapter,
        field_name: str,
        value: object,
    ) -> object | ValidationIssue:
        try:
            return adapter.validate_python(value)
        except ValidationError as exc:
            message = exc.errors()[0]["msg"]
            return ValidationIssue(field=field_name, error=message)

    def _validate_row_rules(self, shipment_row: ShipmentRow) -> list[ValidationIssue]:
        return validate_shipment_draft(
            ShipmentDraft(
                shipment_code=shipment_row.shipment_code,
                customer_name=shipment_row.customer_name,
                origin_city=shipment_row.origin_city,
                destination_city=shipment_row.destination_city,
                weight_kg=shipment_row.weight_kg,
                price=shipment_row.price,
                status=shipment_row.status,
                delivery_date=shipment_row.delivery_date,
            )
        )

    def _validate_partial_row_rules(
        self,
        parsed_values: dict[str, object],
    ) -> list[ValidationIssue]:
        return validate_partial_shipment_draft(
            PartialShipmentDraft(
                shipment_code=self._optional_str(parsed_values.get("shipment_code")),
                customer_name=self._optional_str(parsed_values.get("customer_name")),
                origin_city=self._optional_str(parsed_values.get("origin_city")),
                destination_city=self._optional_str(parsed_values.get("destination_city")),
                weight_kg=self._optional_decimal(parsed_values.get("weight_kg")),
                price=self._optional_decimal(parsed_values.get("price")),
                status=self._optional_str(parsed_values.get("status")),
                delivery_date=self._optional_date(parsed_values.get("delivery_date")),
            )
        )

    def _parsed_shipment_code(self, parsed_values: dict[str, object]) -> str | None:
        return self._optional_str(parsed_values.get("shipment_code"))

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_decimal(value: object) -> Decimal | None:
        return value if isinstance(value, Decimal) else None

    @staticmethod
    def _optional_date(value: object) -> date | None:
        return value if isinstance(value, date) else None

    def _build_import_errors(
        self,
        *,
        import_id: UUID,
        row_number: int,
        raw_data: dict[str, object | None],
        issues: list[ValidationIssue],
    ) -> list[ImportErrorRow]:
        safe_raw_data = jsonb_safe(raw_data)
        return [
            ImportErrorRow(
                id=uuid4(),
                import_id=import_id,
                row_number=row_number,
                field=issue.field,
                error=issue.error,
                raw_data=safe_raw_data,
            )
            for issue in issues
        ]
