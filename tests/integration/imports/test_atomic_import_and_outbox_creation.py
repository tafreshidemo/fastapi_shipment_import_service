from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.models.import_dispatch_outbox import ImportDispatchOutbox
from app.db.models.import_job import ImportJob
from app.imports.repositories.import_repository import ImportRepository


def _new_import_job(job_id) -> ImportJob:
    return ImportJob(
        id=job_id,
        original_file_name="imports.xlsx",
        stored_file_path="/tmp/imports.xlsx",
        file_size_bytes=1024,
        content_type=None,
        idempotency_key=uuid4().hex,
        idempotency_fingerprint="fingerprint-atomic",
        max_attempts=3,
    )


def test_import_job_and_outbox_commit_atomically(step2_session_factory) -> None:
    job_id = uuid4()
    outbox_id = uuid4()

    with step2_session_factory() as session, session.begin():
        job_repository = ImportRepository(session)
        outbox_repository = ImportRepository(session)
        assert job_repository._session is session
        assert outbox_repository._session is session

        persisted_job_id = job_repository.create_import_job(_new_import_job(job_id))
        persisted_outbox_id = outbox_repository.create_dispatch_intent(
            ImportDispatchOutbox(id=outbox_id, import_id=job_id),
        )

    assert persisted_job_id == job_id
    assert persisted_outbox_id == outbox_id

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 1


def test_failed_outbox_insert_rolls_back_import_job(step2_session_factory) -> None:
    job_id = uuid4()
    outbox_id = uuid4()

    with pytest.raises(IntegrityError):
        with step2_session_factory() as session, session.begin():
            job_repository = ImportRepository(session)
            outbox_repository = ImportRepository(session)
            job_repository.create_import_job(_new_import_job(job_id))
            outbox_repository.create_dispatch_intent(
                ImportDispatchOutbox(id=outbox_id, import_id=job_id, status="BROKEN"),
            )

    with step2_session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportJob)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(ImportDispatchOutbox)) == 0
