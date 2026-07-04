from __future__ import annotations

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_valid_and_invalid_rows_commit_as_one_completed_import(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            ["SHP-2", "Beta", "Austin", "Denver", 0, -1, "INVALID", None],
        ],
    )
    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=500),
        worker_id="worker-a",
    ).run(job.id)

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(select(Shipment).where(Shipment.import_id == job.id)).all()
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()

    assert current_job is not None
    assert current_job.status == "COMPLETED"
    assert (current_job.total_rows, current_job.processed_rows) == (2, 2)
    assert (current_job.success_count, current_job.failed_count) == (1, 1)
    assert len(shipments) == 1
    assert len(errors) == 3


def test_structural_workbook_failure_creates_no_row_errors(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "invalid.xlsx"
    workbook_path.write_bytes(b"not-a-workbook")
    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(),
        worker_id="worker-a",
    ).run(job.id)

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()

    assert current_job is not None
    assert current_job.status == "FAILED"
    assert current_job.failure_reason == "Workbook structure could not be processed."
    assert current_job.total_rows == 0
    assert errors == []


def test_missing_required_headers_is_a_terminal_structural_failure(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "missing-headers.xlsx"
    write_workbook(
        workbook_path,
        [],
        headers=[
            "shipment_code",
            "customer_name",
            "origin_city",
            "destination_city",
            "weight_kg",
            "status",
        ],
    )
    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(),
        worker_id="worker-a",
    ).run(job.id)

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(select(Shipment).where(Shipment.import_id == job.id)).all()
        errors = session.scalars(select(ImportError).where(ImportError.import_id == job.id)).all()

    assert current_job is not None
    assert current_job.status == "FAILED"
    assert current_job.failure_reason is not None
    assert "missing required headers" in current_job.failure_reason.lower()
    assert shipments == []
    assert errors == []
    assert (
        current_job.total_rows,
        current_job.processed_rows,
        current_job.success_count,
        current_job.failed_count,
    ) == (0, 0, 0, 0)
