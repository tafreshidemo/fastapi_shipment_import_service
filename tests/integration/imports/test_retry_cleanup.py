from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_retry_cleanup_only_removes_rows_for_the_claimed_import(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-NEW", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    with session_factory() as session:
        target_job = create_import_job(
            session,
            workbook_path=workbook_path,
            attempt_count=1,
        )
        other_job = create_import_job(session, workbook_path=workbook_path)
        session.add_all(
            [
                Shipment(
                    import_id=target_job.id,
                    shipment_code="SHP-OLD",
                    customer_name="Old",
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=Decimal("1"),
                    price=Decimal("1"),
                    status="PENDING",
                ),
                Shipment(
                    import_id=other_job.id,
                    shipment_code="SHP-OTHER",
                    customer_name="Other",
                    origin_city="Austin",
                    destination_city="Denver",
                    weight_kg=Decimal("1"),
                    price=Decimal("1"),
                    status="PENDING",
                ),
                ImportError(
                    id=uuid4(),
                    import_id=target_job.id,
                    row_number=2,
                    field="status",
                    error="old error",
                    raw_data={},
                ),
            ]
        )
        session.commit()

    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(),
        worker_id="worker-a",
    ).run(target_job.id)

    with session_factory() as session:
        target_shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == target_job.id)
        ).all()
        other_shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == other_job.id)
        ).all()
        target_errors = session.scalars(
            select(ImportError).where(ImportError.import_id == target_job.id)
        ).all()

    assert workbook_path.exists()
    assert [shipment.shipment_code for shipment in target_shipments] == ["SHP-NEW"]
    assert [shipment.shipment_code for shipment in other_shipments] == ["SHP-OTHER"]
    assert target_errors == []


def test_operational_error_requeues_then_fails_only_at_max_attempts(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.imports.repositories.shipment_repository import ShipmentRepository
    from app.imports.services.process_import import RetryableImportProcessingError

    workbook_path = tmp_path / "retryable-import.xlsx"
    write_workbook(
        workbook_path,
        [["SHP-RETRY", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None]],
    )
    with session_factory() as session:
        target_job = create_import_job(session, workbook_path=workbook_path)
        other_job = create_import_job(session, workbook_path=workbook_path)
        session.add_all(
            [
                Shipment(
                    import_id=other_job.id,
                    shipment_code="SHP-RETRY-OTHER",
                    customer_name="Other",
                    origin_city="Austin",
                    destination_city="Denver",
                    weight_kg=Decimal("1"),
                    price=Decimal("1"),
                    status="PENDING",
                ),
                ImportError(
                    id=uuid4(),
                    import_id=other_job.id,
                    row_number=2,
                    field="status",
                    error="other import error",
                    raw_data={},
                ),
            ]
        )
        session.commit()

    def raise_operational_error(self, shipments):
        if shipments:
            raise OperationalError("INSERT shipments", {}, RuntimeError("database unavailable"))
        return 0

    monkeypatch.setattr(ShipmentRepository, "bulk_insert", raise_operational_error)
    service = ProcessImportService(
        session_factory=session_factory,
        settings=Settings(import_max_attempts=3),
        worker_id="worker-a",
    )

    with pytest.raises(RetryableImportProcessingError):
        service.run(target_job.id)

    with session_factory() as session:
        first_attempt = session.get(ImportJob, target_job.id)
        assert first_attempt is not None
        assert first_attempt.status == "PENDING"
        assert first_attempt.attempt_count == 1
        assert first_attempt.failure_reason is None
        assert first_attempt.last_failure_reason == (
            "Import processing encountered a temporary database error."
        )
        assert first_attempt.last_requeued_at is None
        session.add_all(
            [
                Shipment(
                    import_id=target_job.id,
                    shipment_code="SHP-STALE",
                    customer_name="Stale",
                    origin_city="Boston",
                    destination_city="Seattle",
                    weight_kg=Decimal("1"),
                    price=Decimal("1"),
                    status="PENDING",
                ),
                ImportError(
                    id=uuid4(),
                    import_id=target_job.id,
                    row_number=2,
                    field="status",
                    error="stale target error",
                    raw_data={},
                ),
            ]
        )
        session.commit()

    with pytest.raises(RetryableImportProcessingError):
        service.run(target_job.id)

    with session_factory() as session:
        second_attempt = session.get(ImportJob, target_job.id)
        target_shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == target_job.id)
        ).all()
        target_errors = session.scalars(
            select(ImportError).where(ImportError.import_id == target_job.id)
        ).all()
        other_shipments = session.scalars(
            select(Shipment).where(Shipment.import_id == other_job.id)
        ).all()
        other_errors = session.scalars(
            select(ImportError).where(ImportError.import_id == other_job.id)
        ).all()

    assert second_attempt is not None
    assert second_attempt.status == "PENDING"
    assert second_attempt.attempt_count == 2
    assert second_attempt.failure_reason is None
    assert second_attempt.last_failure_reason == (
        "Import processing encountered a temporary database error."
    )
    assert second_attempt.last_requeued_at is None
    assert target_shipments == []
    assert target_errors == []
    assert [shipment.shipment_code for shipment in other_shipments] == ["SHP-RETRY-OTHER"]
    assert len(other_errors) == 1

    service.run(target_job.id)

    with session_factory() as session:
        exhausted_job = session.get(ImportJob, target_job.id)

    assert exhausted_job is not None
    assert exhausted_job.status == "FAILED"
    assert exhausted_job.attempt_count == 3
    assert exhausted_job.failure_reason == (
        "Import processing failed after the maximum number of attempts."
    )
    assert exhausted_job.last_failure_reason == exhausted_job.failure_reason
    assert exhausted_job.last_requeued_at is None
