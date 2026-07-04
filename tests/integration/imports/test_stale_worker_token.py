from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, update

from app.core.settings import Settings
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.imports.repositories.import_progress_repository import ImportProgressRepository
from app.imports.services.process_import import ProcessImportService
from tests.support.imports import create_import_job, write_workbook


def test_stale_worker_cannot_update_progress_or_terminal_state(
    session_factory,
    tmp_path,
) -> None:
    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(workbook_path, [])
    current_token = uuid4()
    stale_token = uuid4()

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path, status="PROCESSING")
        job.processing_token = current_token
        job.locked_by_worker = "worker-current"
        session.commit()

    with session_factory() as session:
        repository = ImportProgressRepository(session)
        with session.begin():
            assert not repository.heartbeat(import_id=job.id, processing_token=stale_token)
            assert not repository.complete(
                import_id=job.id,
                processing_token=stale_token,
                total_rows=1,
                success_count=1,
                failed_count=0,
            )
            assert not repository.fail(
                import_id=job.id,
                processing_token=stale_token,
                reason="stale worker",
            )

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)

    assert current_job is not None
    assert current_job.status == "PROCESSING"
    assert current_job.processing_token == current_token


def test_process_service_stops_when_ownership_changes_between_chunks(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """A worker that loses ownership after one committed chunk must stop."""

    workbook_path = tmp_path / "imports.xlsx"
    write_workbook(
        workbook_path,
        [
            ["SHP-1", "Acme", "Boston", "Seattle", 1, 10, "PENDING", None],
            ["SHP-2", "Beta", "Austin", "Denver", 2, 20, "DELIVERED", None],
        ],
    )

    with session_factory() as session:
        job = create_import_job(session, workbook_path=workbook_path)
        session.commit()

    original_process_next_chunk = ProcessImportService._process_next_chunk
    replacement_token = uuid4()
    first_chunk_committed = False

    def process_next_chunk_then_lose_ownership(self, **kwargs):
        nonlocal first_chunk_committed

        result = original_process_next_chunk(self, **kwargs)
        if result is not None and not first_chunk_committed:
            first_chunk_committed = True
            with session_factory() as takeover_session:
                with takeover_session.begin():
                    takeover_session.execute(
                        update(ImportJob)
                        .where(ImportJob.id == job.id)
                        .values(
                            processing_token=replacement_token,
                            locked_by_worker="worker-takeover",
                        )
                    )
        return result

    monkeypatch.setattr(
        ProcessImportService,
        "_process_next_chunk",
        process_next_chunk_then_lose_ownership,
    )

    ProcessImportService(
        session_factory=session_factory,
        settings=Settings(processing_row_chunk_size=1),
        worker_id="worker-a",
    ).run(job.id)

    with session_factory() as session:
        current_job = session.get(ImportJob, job.id)
        shipments = session.scalars(
            select(Shipment)
            .where(Shipment.import_id == job.id)
            .order_by(Shipment.shipment_code)
        ).all()

    assert first_chunk_committed
    assert current_job is not None
    assert current_job.status == "PROCESSING"
    assert current_job.processing_token == replacement_token
    assert current_job.locked_by_worker == "worker-takeover"
    assert (current_job.total_rows, current_job.processed_rows) == (1, 1)
    assert (current_job.success_count, current_job.failed_count) == (1, 0)
    assert [shipment.shipment_code for shipment in shipments] == ["SHP-1"]
