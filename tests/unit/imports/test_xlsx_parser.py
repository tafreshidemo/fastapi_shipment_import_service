from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.imports.parsers.errors import WorkbookStructureError
from app.imports.parsers.xlsx_parser import XlsxParser


def _write_workbook(path: Path, headers: list[str], rows: list[list[object | None]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_valid_workbook_parsing_yields_chunks_and_preserves_row_numbers(tmp_path: Path) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    _write_workbook(
        workbook_path,
        [
            " shipment_code ",
            "customer_name",
            "origin_city",
            "destination_city",
            "weight_kg",
            "price",
            "status",
            "delivery_date",
            "ignored",
        ],
        [
            [
                " SHP-1 ",
                " Acme ",
                " Boston ",
                " Seattle ",
                12.5,
                99.99,
                " PENDING ",
                date(2026, 6, 1),
                "x",
            ],
            [None, None, None, None, None, None, None, None, None],
            [
                "SHP-2",
                "Beta",
                "Austin",
                "Denver",
                5,
                10,
                "DELIVERED",
                None,
                "y",
            ],
            [
                "SHP-3",
                "Gamma",
                "Miami",
                "Dallas",
                2,
                0,
                "IN_TRANSIT",
                None,
                "z",
            ],
        ],
    )

    parser = XlsxParser(workbook_path, chunk_size=2)
    chunks = list(parser.iter_chunks())

    assert [len(chunk) for chunk in chunks] == [2, 1]
    assert [row.row_number for row in chunks[0]] == [2, 4]
    assert [row.row_number for row in chunks[1]] == [5]
    assert chunks[0][0].values["shipment_code"] == "SHP-1"
    assert chunks[0][0].values["customer_name"] == "Acme"
    assert chunks[0][0].values["delivery_date"] == datetime(2026, 6, 1, 0, 0)
    assert "ignored" not in chunks[0][0].values


def test_missing_required_headers_raises_workbook_structure_error(tmp_path: Path) -> None:
    workbook_path = tmp_path / "missing.xlsx"
    _write_workbook(
        workbook_path,
        [
            "shipment_code",
            "customer_name",
            "origin_city",
            "destination_city",
            "weight_kg",
            "status",
        ],
        [],
    )

    parser = XlsxParser(workbook_path)

    with pytest.raises(WorkbookStructureError, match="missing required headers"):
        list(parser.iter_chunks())


def test_duplicate_headers_raise_workbook_structure_error(tmp_path: Path) -> None:
    workbook_path = tmp_path / "duplicate.xlsx"
    _write_workbook(
        workbook_path,
        [
            "shipment_code",
            "customer_name",
            "shipment_code",
            "destination_city",
            "weight_kg",
            "price",
            "status",
        ],
        [],
    )

    parser = XlsxParser(workbook_path)

    with pytest.raises(WorkbookStructureError, match="duplicate headers"):
        list(parser.iter_chunks())


def test_invalid_workbook_raises_workbook_structure_error(tmp_path: Path) -> None:
    workbook_path = tmp_path / "invalid.xlsx"
    workbook_path.write_bytes(b"not-a-zip")

    parser = XlsxParser(workbook_path)

    with pytest.raises(WorkbookStructureError, match="could not be processed"):
        list(parser.iter_chunks())


def test_xlsx_parser_does_not_import_domain_shipment_rules() -> None:
    parser_source = (
        Path(__file__).resolve().parents[3]
        / "app"
        / "imports"
        / "parsers"
        / "xlsx_parser.py"
    ).read_text()

    assert "app.domain.shipment_rules" not in parser_source
    assert "REQUIRED_SHIPMENT_FIELDS" not in parser_source
