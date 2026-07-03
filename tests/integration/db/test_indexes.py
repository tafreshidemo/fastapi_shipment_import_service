from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect


def _index_map(
    inspector: sa.Inspector,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    return {index["name"]: index for index in inspector.get_indexes(table_name)}


def test_import_job_indexes_exist_with_expected_column_order(
    step2_migrated_database_url: str,
) -> None:
    engine = create_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            indexes = _index_map(
                inspect(connection),
                "import_jobs",
            )
    finally:
        engine.dispose()

    assert indexes["ix_import_jobs_status"]["column_names"] == [
        "status",
    ]
    assert indexes["ix_import_jobs_status_last_heartbeat_at"]["column_names"] == [
        "status",
        "last_heartbeat_at",
    ]

    assert indexes["ix_import_jobs_status"]["unique"] is False
    assert indexes["ix_import_jobs_status_last_heartbeat_at"]["unique"] is False


def test_outbox_indexes_exist_with_expected_column_order(
    step2_migrated_database_url: str,
) -> None:
    engine = create_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            indexes = _index_map(
                inspect(connection),
                "import_dispatch_outbox",
            )
    finally:
        engine.dispose()

    assert indexes["ix_import_dispatch_outbox_status_available_at"]["column_names"] == [
        "status",
        "available_at",
    ]

    assert indexes["ix_import_dispatch_outbox_status_claimed_at"]["column_names"] == [
        "status",
        "claimed_at",
    ]

    assert indexes["ix_import_dispatch_outbox_status_available_at"]["unique"] is False

    assert indexes["ix_import_dispatch_outbox_status_claimed_at"]["unique"] is False


def test_import_error_index_exists_with_expected_column_order(
    step2_migrated_database_url: str,
) -> None:
    engine = create_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            indexes = _index_map(
                inspect(connection),
                "import_errors",
            )
    finally:
        engine.dispose()

    assert indexes["ix_import_errors_import_id_row_number_id"]["column_names"] == [
        "import_id",
        "row_number",
        "id",
    ]

    assert indexes["ix_import_errors_import_id_row_number_id"]["unique"] is False


def test_shipment_code_unique_constraint_creates_global_unique_index(
    step2_migrated_database_url: str,
) -> None:
    engine = create_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)

            unique_constraints = {
                constraint["name"]: constraint
                for constraint in inspector.get_unique_constraints("shipments")
            }

            indexes = _index_map(
                inspector,
                "shipments",
            )
    finally:
        engine.dispose()

    assert unique_constraints["uq_shipments_shipment_code"]["column_names"] == [
        "shipment_code",
    ]

    duplicate_constraint_index = indexes.get("uq_shipments_shipment_code")

    if duplicate_constraint_index is not None:
        assert duplicate_constraint_index["column_names"] == [
            "shipment_code",
        ]
        assert duplicate_constraint_index["unique"] is True
