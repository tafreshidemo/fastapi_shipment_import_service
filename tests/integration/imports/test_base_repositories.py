from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_error import ImportError as ImportErrorRow
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories import (
    ImportErrorRepository,
    ImportRepository,
    ShipmentRepository,
)


def _make_job(
    job_id: UUID | None = None,
) -> ImportJob:
    return ImportJob(
        id=job_id or uuid4(),
        original_file_name="imports.xlsx",
        stored_file_path="/tmp/imports.xlsx",
        file_size_bytes=1024,
        content_type=None,
        idempotency_key=uuid4().hex,
        idempotency_fingerprint=f"fingerprint-{uuid4().hex}",
        max_attempts=3,
    )


def _make_shipment(
    import_id: UUID,
    shipment_code: str,
) -> Shipment:
    return Shipment(
        import_id=import_id,
        shipment_code=shipment_code,
        customer_name="Acme",
        origin_city="Tehran",
        destination_city="Berlin",
        weight_kg=Decimal("1.250"),
        price=Decimal("10.00"),
        status="PENDING",
    )


def _make_import_error(
    import_id: UUID,
    row_number: int,
) -> ImportErrorRow:
    return ImportErrorRow(
        import_id=import_id,
        row_number=row_number,
        field="shipment_code",
        error="missing value",
        raw_data={"shipment_code": None},
    )


def test_repositories_use_caller_session_without_transaction_ownership(
    step2_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = step2_session_factory()
    original_close = session.close

    calls = {
        "commit": 0,
        "rollback": 0,
        "close": 0,
    }

    def forbidden(method_name: str):
        def fail() -> None:
            calls[method_name] += 1
            raise AssertionError(f"{method_name} must not be called by a repository")

        return fail

    monkeypatch.setattr(
        session,
        "commit",
        forbidden("commit"),
    )
    monkeypatch.setattr(
        session,
        "rollback",
        forbidden("rollback"),
    )
    monkeypatch.setattr(
        session,
        "close",
        forbidden("close"),
    )

    try:
        import_repository = ImportRepository(session)

        job = _make_job()

        persisted_job_id = import_repository.create_import_job(job)

        assert persisted_job_id == job.id
        assert import_repository.get_status_by_id(job.id) == "PENDING"
        assert import_repository.get_status_by_id(uuid4()) is None
        assert import_repository.get_id_by_idempotency_key(job.idempotency_key) == job.id

        outbox_id = import_repository.create_dispatch_intent(ImportDispatchOutbox(import_id=job.id))

        assert isinstance(outbox_id, UUID)
        assert calls == {
            "commit": 0,
            "rollback": 0,
            "close": 0,
        }
    finally:
        monkeypatch.setattr(
            session,
            "close",
            original_close,
        )
        session.close()


def test_import_job_status_lookup_returns_plain_scalar(
    step2_session_factory,
) -> None:
    with step2_session_factory() as session, session.begin():
        job = _make_job()
        session.add(job)
        session.flush()

        repository = ImportRepository(session)

        status = repository.get_status_by_id(job.id)
        missing_status = repository.get_status_by_id(uuid4())

    assert status == "PENDING"
    assert isinstance(status, str)
    assert not isinstance(status, ImportJob)
    assert missing_status is None


def test_bulk_repositories_insert_multiple_rows_with_one_flush_per_call(
    step2_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = step2_session_factory()

    original_add_all = session.add_all
    original_flush = session.flush
    original_close = session.close

    calls = {
        "add_all": 0,
        "flush": 0,
        "commit": 0,
        "rollback": 0,
        "close": 0,
    }

    def recording_add_all(
        instances: object,
    ) -> None:
        calls["add_all"] += 1
        original_add_all(instances)

    def recording_flush(
        *args: object,
        **kwargs: object,
    ) -> None:
        calls["flush"] += 1
        original_flush(*args, **kwargs)

    def forbidden(method_name: str):
        def fail() -> None:
            calls[method_name] += 1
            raise AssertionError(f"{method_name} must not be called by a repository")

        return fail

    monkeypatch.setattr(
        session,
        "add_all",
        recording_add_all,
    )
    monkeypatch.setattr(
        session,
        "flush",
        recording_flush,
    )
    monkeypatch.setattr(
        session,
        "commit",
        forbidden("commit"),
    )
    monkeypatch.setattr(
        session,
        "rollback",
        forbidden("rollback"),
    )
    monkeypatch.setattr(
        session,
        "close",
        forbidden("close"),
    )

    job = _make_job()

    try:
        transaction = session.begin()

        session.add(job)

        # Setup flush is intentionally excluded from repository assertions.
        original_flush()

        calls["add_all"] = 0
        calls["flush"] = 0

        shipment_repository = ShipmentRepository(session)
        import_error_repository = ImportErrorRepository(session)

        shipments = [
            _make_shipment(job.id, "BULK-SHIP-1"),
            _make_shipment(job.id, "BULK-SHIP-2"),
            _make_shipment(job.id, "BULK-SHIP-3"),
        ]

        import_errors = [
            _make_import_error(job.id, row_number=2),
            _make_import_error(job.id, row_number=3),
        ]

        inserted_shipments = shipment_repository.bulk_insert(shipments)

        assert inserted_shipments == 3
        assert calls["add_all"] == 1
        assert calls["flush"] == 1

        inserted_errors = import_error_repository.bulk_insert(import_errors)

        assert inserted_errors == 2
        assert calls["add_all"] == 2
        assert calls["flush"] == 2

        assert calls["commit"] == 0
        assert calls["rollback"] == 0
        assert calls["close"] == 0

        # Transaction ownership belongs to the caller, not repositories.
        transaction.commit()
    finally:
        monkeypatch.setattr(
            session,
            "close",
            original_close,
        )
        session.close()

    with step2_session_factory() as verification_session:
        shipment_count = verification_session.scalar(
            sa.select(sa.func.count()).select_from(Shipment).where(Shipment.import_id == job.id)
        )

        import_error_count = verification_session.scalar(
            sa.select(sa.func.count())
            .select_from(ImportErrorRow)
            .where(ImportErrorRow.import_id == job.id)
        )

    assert shipment_count == 3
    assert import_error_count == 2



def test_import_error_repository_makes_raw_data_jsonb_safe(step2_session_factory) -> None:
    from datetime import date, datetime, timezone

    with step2_session_factory() as session, session.begin():
        job = _make_job()
        session.add(job)
        session.flush()

        repository = ImportErrorRepository(session)
        repository.bulk_insert(
            [
                ImportErrorRow(
                    import_id=job.id,
                    row_number=2,
                    field="delivery_date",
                    error="invalid row",
                    raw_data={
                        "weight_kg": Decimal("12.345"),
                        "price": Decimal("99.99"),
                        "delivery_date": date(2026, 7, 4),
                        "seen_at": datetime(2026, 7, 4, 12, 30, tzinfo=timezone.utc),
                    },
                )
            ]
        )

    with step2_session_factory() as session:
        raw_data = session.scalar(sa.select(ImportErrorRow.raw_data))

    assert raw_data == {
        "weight_kg": "12.345",
        "price": "99.99",
        "delivery_date": "2026-07-04",
        "seen_at": "2026-07-04T12:30:00+00:00",
    }

def test_empty_bulk_insert_does_not_flush(
    step2_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = step2_session_factory()

    flush_calls = 0

    def recording_flush(
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal flush_calls
        flush_calls += 1

    monkeypatch.setattr(
        session,
        "flush",
        recording_flush,
    )

    try:
        shipment_repository = ShipmentRepository(session)
        import_error_repository = ImportErrorRepository(session)

        assert shipment_repository.bulk_insert([]) == 0
        assert import_error_repository.bulk_insert([]) == 0
        assert flush_calls == 0
    finally:
        session.close()


def test_duplicate_shipment_lookup_uses_one_batch_query(
    step2_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = step2_session_factory()

    try:
        job = _make_job()
        session.add(job)
        session.flush()

        session.add(
            _make_shipment(
                job.id,
                "SHIP-EXISTING",
            )
        )
        session.commit()

        original_execute = session.execute
        execute_calls = 0

        def recording_execute(
            *args: object,
            **kwargs: object,
        ):
            nonlocal execute_calls
            execute_calls += 1
            return original_execute(*args, **kwargs)

        monkeypatch.setattr(
            session,
            "execute",
            recording_execute,
        )

        repository = ShipmentRepository(session)

        result = repository.find_existing_shipment_codes(
            {
                "SHIP-EXISTING",
                "SHIP-MISSING-1",
                "SHIP-MISSING-2",
            }
        )

        assert result == {"SHIP-EXISTING"}
        assert execute_calls == 1
    finally:
        session.close()


def test_duplicate_lookup_with_empty_input_does_not_query_database(
    step2_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = step2_session_factory()

    execute_calls = 0

    def unexpected_execute(
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal execute_calls
        execute_calls += 1
        raise AssertionError("empty duplicate lookup must not query PostgreSQL")

    monkeypatch.setattr(
        session,
        "execute",
        unexpected_execute,
    )

    try:
        repository = ShipmentRepository(session)

        assert repository.find_existing_shipment_codes(set()) == set()
        assert execute_calls == 0
    finally:
        session.close()


def test_no_future_repository_modules_exist() -> None:
    project_root = Path(__file__).resolve().parents[3]

    future_modules = [
        (project_root / "app" / "imports" / "repositories" / "import_claim_repository.py"),
        (project_root / "app" / "imports" / "repositories" / "import_progress_repository.py"),
        (project_root / "app" / "outbox" / "repositories" / "outbox_repository.py"),
        (project_root / "app" / "imports" / "repositories" / "import_job_repository.py"),
        (
            project_root
            / "app"
            / "imports"
            / "repositories"
            / "import_dispatch_outbox_repository.py"
        ),
    ]

    assert all(not module_path.exists() for module_path in future_modules)
