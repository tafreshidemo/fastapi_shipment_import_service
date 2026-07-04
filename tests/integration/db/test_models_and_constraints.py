from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DataError, IntegrityError

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError as ImportErrorRow
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from tests.support.postgres_database import run_alembic

STEP2_REVISION = "0002_step2_models"


EXPECTED_TABLES = {
    "import_jobs",
    "import_dispatch_outbox",
    "shipments",
    "import_errors",
}


def _new_engine(database_url: str) -> sa.Engine:
    return create_engine(database_url)


def _fingerprint(seed: str) -> str:
    value = f"{seed}-{uuid4().hex}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_import_job(
    *,
    file_name: str = "imports.xlsx",
    idempotency_key: str | None = None,
    content_type: str | None = None,
    status: str = "PENDING",
    failure_reason: str | None = None,
) -> ImportJob:
    return ImportJob(
        original_file_name=file_name,
        stored_file_path=f"/tmp/{file_name}",
        file_size_bytes=1024,
        content_type=content_type,
        idempotency_key=idempotency_key,
        idempotency_fingerprint=_fingerprint(file_name),
        max_attempts=3,
        status=status,
        failure_reason=failure_reason,
    )


def _new_shipment(
    import_id: UUID,
    *,
    shipment_code: str | None = None,
    customer_name: str = "Acme",
    weight_kg: Decimal = Decimal("1.250"),
    price: Decimal = Decimal("10.00"),
    status: str = "PENDING",
) -> Shipment:
    return Shipment(
        import_id=import_id,
        shipment_code=shipment_code or f"SHIP-{uuid4().hex}",
        customer_name=customer_name,
        origin_city="Tehran",
        destination_city="Berlin",
        weight_kg=weight_kg,
        price=price,
        status=status,
    )


def _create_import_job(step2_session_factory) -> UUID:
    with step2_session_factory() as session, session.begin():
        job = _new_import_job()
        session.add(job)
        session.flush()

        return job.id


def _column_map(
    inspector: sa.Inspector,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    return {
        column["name"]: column
        for column in inspector.get_columns(table_name)
    }


def _constraint_names(
    constraints: list[dict[str, Any]],
) -> set[str]:
    return {
        constraint["name"]
        for constraint in constraints
        if constraint.get("name")
    }


def _normalized_default(column: dict[str, Any]) -> str | None:
    default = column.get("default")

    if default is None:
        return None

    return (
        str(default)
        .lower()
        .replace("::character varying", "")
        .replace("::text", "")
        .replace("::integer", "")
        .strip()
        .strip("'")
    )


def _assert_string_column(
    column: dict[str, Any],
    *,
    length: int,
    nullable: bool,
) -> None:
    assert isinstance(column["type"], sa.String)
    assert column["type"].length == length
    assert column["nullable"] is nullable


def _assert_integer_column(
    column: dict[str, Any],
    *,
    nullable: bool,
    server_default: str | None = None,
) -> None:
    assert isinstance(column["type"], sa.Integer)
    assert column["nullable"] is nullable

    if server_default is not None:
        assert _normalized_default(column) == server_default


def _assert_uuid_column(
    column: dict[str, Any],
    *,
    nullable: bool,
) -> None:
    assert isinstance(column["type"], sa.Uuid)
    assert column["nullable"] is nullable


def _assert_datetime_column(
    column: dict[str, Any],
    *,
    nullable: bool,
    has_now_default: bool = False,
) -> None:
    column_type = column["type"]

    assert isinstance(column_type, sa.DateTime)
    assert column_type.timezone is True
    assert column["nullable"] is nullable

    if has_now_default:
        default = _normalized_default(column)

        assert default is not None
        assert "now()" in default


def _assert_import_job_foreign_key(
    inspector: sa.Inspector,
    table_name: str,
) -> None:
    foreign_keys = inspector.get_foreign_keys(table_name)

    assert len(foreign_keys) == 1

    foreign_key = foreign_keys[0]

    assert foreign_key["constrained_columns"] == ["import_id"]
    assert foreign_key["referred_table"] == "import_jobs"
    assert foreign_key["referred_columns"] == ["id"]
    assert foreign_key["options"].get("ondelete") == "CASCADE"


def test_step2_migration_upgrade_downgrade_upgrade_lifecycle(
    step2_database_url: str,
) -> None:
    project_root = Path(__file__).resolve().parents[3]

    run_alembic(
        project_root,
        step2_database_url,
        "upgrade",
        STEP2_REVISION,
    )

    engine = _new_engine(step2_database_url)

    try:
        with engine.connect() as connection:
            table_names = set(
                inspect(connection).get_table_names()
            )

            assert EXPECTED_TABLES.issubset(table_names)
            assert "alembic_version" in table_names
    finally:
        engine.dispose()

    run_alembic(
        project_root,
        step2_database_url,
        "downgrade",
        "-1",
    )

    engine = _new_engine(step2_database_url)

    try:
        with engine.connect() as connection:
            table_names = set(
                inspect(connection).get_table_names()
            )

            assert EXPECTED_TABLES.isdisjoint(table_names)
            assert "alembic_version" in table_names
    finally:
        engine.dispose()

    run_alembic(
        project_root,
        step2_database_url,
        "upgrade",
        STEP2_REVISION,
    )

    engine = _new_engine(step2_database_url)

    try:
        with engine.connect() as connection:
            table_names = set(
                inspect(connection).get_table_names()
            )

            assert EXPECTED_TABLES.issubset(table_names)
    finally:
        engine.dispose()


def test_all_step2_tables_exist(
    step2_migrated_database_url: str,
) -> None:
    engine = _new_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            table_names = set(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()

    assert EXPECTED_TABLES.issubset(table_names)


def test_import_jobs_schema_matches_model_and_migration(
    step2_migrated_database_url: str,
) -> None:
    engine = _new_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = _column_map(
                inspector,
                "import_jobs",
            )

            assert list(columns) == [
                column.name
                for column in ImportJob.__table__.columns
            ]

            _assert_uuid_column(
                columns["id"],
                nullable=False,
            )

            _assert_string_column(
                columns["status"],
                length=32,
                nullable=False,
            )
            assert _normalized_default(
                columns["status"]
            ) == "pending"

            _assert_string_column(
                columns["original_file_name"],
                length=255,
                nullable=False,
            )
            _assert_string_column(
                columns["stored_file_path"],
                length=1024,
                nullable=False,
            )
            _assert_integer_column(
                columns["file_size_bytes"],
                nullable=False,
            )
            _assert_string_column(
                columns["content_type"],
                length=255,
                nullable=True,
            )
            _assert_string_column(
                columns["idempotency_key"],
                length=255,
                nullable=True,
            )
            _assert_string_column(
                columns["idempotency_fingerprint"],
                length=64,
                nullable=False,
            )

            for counter_name in (
                "total_rows",
                "processed_rows",
                "success_count",
                "failed_count",
                "attempt_count",
            ):
                _assert_integer_column(
                    columns[counter_name],
                    nullable=False,
                    server_default="0",
                )

            _assert_integer_column(
                columns["max_attempts"],
                nullable=False,
            )
            assert columns["max_attempts"]["default"] is None

            _assert_uuid_column(
                columns["processing_token"],
                nullable=True,
            )
            _assert_string_column(
                columns["locked_by_worker"],
                length=255,
                nullable=True,
            )

            for column_name in (
                "started_at",
                "last_heartbeat_at",
                "finished_at",
                "last_requeued_at",
            ):
                _assert_datetime_column(
                    columns[column_name],
                    nullable=True,
                )

            assert isinstance(
                columns["last_failure_reason"]["type"],
                sa.Text,
            )
            assert (
                columns["last_failure_reason"]["nullable"]
                is True
            )

            assert isinstance(
                columns["failure_reason"]["type"],
                sa.Text,
            )
            assert columns["failure_reason"]["nullable"] is True

            _assert_datetime_column(
                columns["created_at"],
                nullable=False,
                has_now_default=True,
            )
            _assert_datetime_column(
                columns["updated_at"],
                nullable=False,
                has_now_default=True,
            )

            unique_constraints = (
                inspector.get_unique_constraints(
                    "import_jobs"
                )
            )
            assert _constraint_names(
                unique_constraints
            ) == {
                "uq_import_jobs_idempotency_key",
            }

            check_constraints = (
                inspector.get_check_constraints(
                    "import_jobs"
                )
            )
            assert _constraint_names(
                check_constraints
            ) == {
                "ck_import_jobs_status",
                "ck_import_jobs_failure_reason_terminal",
            }
    finally:
        engine.dispose()


def test_outbox_schema_matches_model_and_migration(
    step2_migrated_database_url: str,
) -> None:
    engine = _new_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = _column_map(
                inspector,
                "import_dispatch_outbox",
            )

            assert list(columns) == [
                column.name
                for column in (
                    ImportDispatchOutbox.__table__.columns
                )
            ]

            _assert_uuid_column(
                columns["id"],
                nullable=False,
            )
            _assert_uuid_column(
                columns["import_id"],
                nullable=False,
            )

            _assert_string_column(
                columns["status"],
                length=32,
                nullable=False,
            )
            assert _normalized_default(
                columns["status"]
            ) == "pending"

            _assert_integer_column(
                columns["attempt_count"],
                nullable=False,
                server_default="0",
            )

            _assert_datetime_column(
                columns["available_at"],
                nullable=False,
                has_now_default=True,
            )
            _assert_datetime_column(
                columns["claimed_at"],
                nullable=True,
            )
            _assert_uuid_column(
                columns["claim_token"],
                nullable=True,
            )
            _assert_datetime_column(
                columns["published_at"],
                nullable=True,
            )

            assert isinstance(
                columns["last_error"]["type"],
                sa.Text,
            )
            assert columns["last_error"]["nullable"] is True

            _assert_datetime_column(
                columns["created_at"],
                nullable=False,
                has_now_default=True,
            )
            _assert_datetime_column(
                columns["updated_at"],
                nullable=False,
                has_now_default=True,
            )

            _assert_import_job_foreign_key(
                inspector,
                "import_dispatch_outbox",
            )

            check_constraints = (
                inspector.get_check_constraints(
                    "import_dispatch_outbox"
                )
            )
            assert _constraint_names(
                check_constraints
            ) == {
                "ck_import_dispatch_outbox_status",
            }
    finally:
        engine.dispose()


def test_shipments_schema_matches_model_and_migration(
    step2_migrated_database_url: str,
) -> None:
    engine = _new_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = _column_map(
                inspector,
                "shipments",
            )

            assert list(columns) == [
                column.name
                for column in Shipment.__table__.columns
            ]

            _assert_uuid_column(
                columns["id"],
                nullable=False,
            )
            _assert_uuid_column(
                columns["import_id"],
                nullable=False,
            )

            _assert_string_column(
                columns["shipment_code"],
                length=128,
                nullable=False,
            )
            _assert_string_column(
                columns["customer_name"],
                length=150,
                nullable=False,
            )
            _assert_string_column(
                columns["origin_city"],
                length=255,
                nullable=False,
            )
            _assert_string_column(
                columns["destination_city"],
                length=255,
                nullable=False,
            )

            weight_type = columns["weight_kg"]["type"]

            assert isinstance(weight_type, sa.Numeric)
            assert weight_type.precision == 12
            assert weight_type.scale == 3
            assert columns["weight_kg"]["nullable"] is False

            price_type = columns["price"]["type"]

            assert isinstance(price_type, sa.Numeric)
            assert price_type.precision == 18
            assert price_type.scale == 2
            assert columns["price"]["nullable"] is False

            _assert_string_column(
                columns["status"],
                length=32,
                nullable=False,
            )
            assert _normalized_default(
                columns["status"]
            ) == "pending"

            assert isinstance(
                columns["delivery_date"]["type"],
                sa.Date,
            )
            assert columns["delivery_date"]["nullable"] is True

            _assert_datetime_column(
                columns["created_at"],
                nullable=False,
                has_now_default=True,
            )
            _assert_datetime_column(
                columns["updated_at"],
                nullable=False,
                has_now_default=True,
            )

            _assert_import_job_foreign_key(
                inspector,
                "shipments",
            )

            unique_constraints = (
                inspector.get_unique_constraints(
                    "shipments"
                )
            )
            assert _constraint_names(
                unique_constraints
            ) == {
                "uq_shipments_shipment_code",
            }

            check_constraints = (
                inspector.get_check_constraints(
                    "shipments"
                )
            )
            assert _constraint_names(
                check_constraints
            ) == {
                "ck_shipments_status",
                "ck_shipments_weight_kg_positive",
                "ck_shipments_price_non_negative",
            }
    finally:
        engine.dispose()


def test_import_errors_schema_matches_model_and_migration(
    step2_migrated_database_url: str,
) -> None:
    engine = _new_engine(step2_migrated_database_url)

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            columns = _column_map(
                inspector,
                "import_errors",
            )

            assert list(columns) == [
                column.name
                for column in (
                    ImportErrorRow.__table__.columns
                )
            ]

            _assert_uuid_column(
                columns["id"],
                nullable=False,
            )
            _assert_uuid_column(
                columns["import_id"],
                nullable=False,
            )
            _assert_integer_column(
                columns["row_number"],
                nullable=False,
            )
            _assert_string_column(
                columns["field"],
                length=255,
                nullable=False,
            )

            assert isinstance(
                columns["error"]["type"],
                sa.Text,
            )
            assert columns["error"]["nullable"] is False

            assert isinstance(
                columns["raw_data"]["type"],
                JSONB,
            )
            assert columns["raw_data"]["nullable"] is False

            _assert_datetime_column(
                columns["created_at"],
                nullable=False,
                has_now_default=True,
            )

            _assert_import_job_foreign_key(
                inspector,
                "import_errors",
            )
    finally:
        engine.dispose()


def test_import_job_server_defaults_are_persisted(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        job = _new_import_job(
            content_type=None,
            idempotency_key=None,
        )
        session.add(job)
        session.flush()

        assert job.status == "PENDING"
        assert job.total_rows == 0
        assert job.processed_rows == 0
        assert job.success_count == 0
        assert job.failed_count == 0
        assert job.attempt_count == 0

        assert job.content_type is None
        assert job.idempotency_key is None
        assert job.processing_token is None
        assert job.locked_by_worker is None
        assert job.started_at is None
        assert job.last_heartbeat_at is None
        assert job.finished_at is None
        assert job.last_failure_reason is None
        assert job.failure_reason is None
        assert job.last_requeued_at is None

        assert job.created_at is not None
        assert job.updated_at is not None


def test_outbox_server_defaults_and_nullable_fields_are_persisted(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with step2_session_factory() as session, session.begin():
        outbox = ImportDispatchOutbox(
            import_id=import_id,
        )
        session.add(outbox)
        session.flush()

        assert outbox.status == "PENDING"
        assert outbox.attempt_count == 0
        assert outbox.available_at is not None
        assert outbox.claimed_at is None
        assert outbox.claim_token is None
        assert outbox.published_at is None
        assert outbox.last_error is None
        assert outbox.created_at is not None
        assert outbox.updated_at is not None


def test_idempotency_key_rejects_duplicate_non_null_values(
    step2_session_factory,
) -> None:
    duplicate_key = "same-idempotency-key"

    with step2_session_factory() as session, session.begin():
        session.add(
            _new_import_job(
                file_name="first.xlsx",
                idempotency_key=duplicate_key,
            )
        )

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_import_job(
                    file_name="second.xlsx",
                    idempotency_key=duplicate_key,
                )
            )
            session.flush()


def test_idempotency_key_allows_multiple_null_values(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        session.add_all(
            [
                _new_import_job(
                    file_name="null-key-1.xlsx",
                ),
                _new_import_job(
                    file_name="null-key-2.xlsx",
                ),
            ]
        )

    with step2_session_factory() as session:
        null_key_count = session.scalar(
            select(sa.func.count())
            .select_from(ImportJob)
            .where(
                ImportJob.idempotency_key.is_(None)
            )
        )

    assert null_key_count == 2


def test_shipment_code_is_globally_unique_across_imports(
    step2_session_factory,
) -> None:
    first_import_id = _create_import_job(
        step2_session_factory
    )
    second_import_id = _create_import_job(
        step2_session_factory
    )

    with step2_session_factory() as session, session.begin():
        session.add(
            _new_shipment(
                first_import_id,
                shipment_code="GLOBAL-SHIPMENT-001",
            )
        )

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_shipment(
                    second_import_id,
                    shipment_code="GLOBAL-SHIPMENT-001",
                )
            )
            session.flush()


def test_positive_weight_is_accepted(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with step2_session_factory() as session, session.begin():
        shipment = _new_shipment(
            import_id,
            shipment_code="POSITIVE-WEIGHT",
            weight_kg=Decimal("0.001"),
        )
        session.add(shipment)
        session.flush()

        assert shipment.id is not None


@pytest.mark.parametrize(
    "invalid_weight",
    [
        Decimal("0"),
        Decimal("-0.001"),
    ],
)
def test_zero_and_negative_weight_are_rejected(
    step2_session_factory,
    invalid_weight: Decimal,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_shipment(
                    import_id,
                    weight_kg=invalid_weight,
                )
            )
            session.flush()


def test_zero_price_is_accepted(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with step2_session_factory() as session, session.begin():
        shipment = _new_shipment(
            import_id,
            shipment_code="ZERO-PRICE",
            price=Decimal("0.00"),
        )
        session.add(shipment)
        session.flush()

        assert shipment.id is not None


def test_negative_price_is_rejected(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_shipment(
                    import_id,
                    price=Decimal("-0.01"),
                )
            )
            session.flush()


def test_invalid_shipment_status_is_rejected(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_shipment(
                    import_id,
                    status="UNKNOWN",
                )
            )
            session.flush()


def test_invalid_import_job_status_is_rejected(
    step2_session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_import_job(
                    status="UNKNOWN",
                )
            )
            session.flush()


def test_invalid_outbox_status_is_rejected(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)

    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                ImportDispatchOutbox(
                    import_id=import_id,
                    status="UNKNOWN",
                )
            )
            session.flush()


def test_failed_status_without_failure_reason_is_rejected(
    step2_session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_import_job(
                    status="FAILED",
                    failure_reason=None,
                )
            )
            session.flush()


def test_failed_status_with_failure_reason_is_accepted(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        job = _new_import_job(
            status="FAILED",
            failure_reason="terminal processing failure",
        )
        session.add(job)
        session.flush()

        assert job.id is not None


def test_non_failed_status_with_failure_reason_is_rejected(
    step2_session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_import_job(
                    status="PENDING",
                    failure_reason="must be null",
                )
            )
            session.flush()


def test_non_failed_status_without_failure_reason_is_accepted(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        job = _new_import_job(
            status="PROCESSING",
            failure_reason=None,
        )
        session.add(job)
        session.flush()

        assert job.id is not None


def test_customer_name_accepts_150_characters(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)
    valid_name = "x" * 150

    with step2_session_factory() as session, session.begin():
        shipment = _new_shipment(
            import_id,
            shipment_code="CUSTOMER-NAME-150",
            customer_name=valid_name,
        )
        session.add(shipment)
        session.flush()

        assert shipment.customer_name == valid_name


def test_customer_name_rejects_151_characters(
    step2_session_factory,
) -> None:
    import_id = _create_import_job(step2_session_factory)
    invalid_name = "x" * 151

    with pytest.raises(DataError):
        with (
            step2_session_factory() as session,
            session.begin(),
        ):
            session.add(
                _new_shipment(
                    import_id,
                    shipment_code="CUSTOMER-NAME-151",
                    customer_name=invalid_name,
                )
            )
            session.flush()


def test_deleting_import_job_cascades_to_all_child_tables(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        job = _new_import_job()
        session.add(job)
        session.flush()

        job_id = job.id

        session.add_all(
            [
                ImportDispatchOutbox(
                    import_id=job_id,
                ),
                _new_shipment(
                    job_id,
                    shipment_code="CASCADE-SHIPMENT",
                ),
                ImportErrorRow(
                    import_id=job_id,
                    row_number=2,
                    field="price",
                    error="invalid price",
                    raw_data={
                        "price": "invalid",
                    },
                ),
            ]
        )

    with step2_session_factory() as session, session.begin():
        session.execute(
            sa.delete(ImportJob).where(
                ImportJob.id == job_id
            )
        )

    with step2_session_factory() as session:
        outbox_count = session.scalar(
            select(sa.func.count())
            .select_from(ImportDispatchOutbox)
            .where(
                ImportDispatchOutbox.import_id == job_id
            )
        )
        shipment_count = session.scalar(
            select(sa.func.count())
            .select_from(Shipment)
            .where(
                Shipment.import_id == job_id
            )
        )
        error_count = session.scalar(
            select(sa.func.count())
            .select_from(ImportErrorRow)
            .where(
                ImportErrorRow.import_id == job_id
            )
        )

    assert outbox_count == 0
    assert shipment_count == 0
    assert error_count == 0