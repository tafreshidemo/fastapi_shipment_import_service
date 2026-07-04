from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.api.app import create_app
from app.core.settings import Settings, get_settings
from app.db.models.import_error import ImportError
from app.db.models.import_job import ImportJob
from app.db.models.shipment import Shipment
from app.db.session import get_session_factory


def build_query_app(session_factory: sessionmaker[Session]) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: Settings()
    application.dependency_overrides[get_session_factory] = lambda: session_factory
    return application


def add_import(
    session: Session,
    *,
    status: str = "PENDING",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    last_failure_reason: str | None = None,
    failure_reason: str | None = None,
    import_id: UUID | None = None,
) -> ImportJob:
    created = created_at or datetime.now(UTC)
    job = ImportJob(
        id=import_id or uuid4(),
        status=status,
        original_file_name=f"{uuid4().hex}.xlsx",
        stored_file_path=f"/tmp/{uuid4().hex}.xlsx",
        file_size_bytes=1024,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        idempotency_key=None,
        idempotency_fingerprint=uuid4().hex + uuid4().hex,
        total_rows=0,
        processed_rows=0,
        success_count=0,
        failed_count=0,
        attempt_count=0,
        max_attempts=3,
        created_at=created,
        started_at=started_at,
        finished_at=finished_at,
        last_failure_reason=last_failure_reason,
        failure_reason=failure_reason,
    )
    session.add(job)
    session.flush()
    return job


def add_import_error(
    session: Session,
    *,
    import_id: UUID,
    row_number: int,
    field: str,
    error: str,
) -> ImportError:
    row = ImportError(
        id=uuid4(),
        import_id=import_id,
        row_number=row_number,
        field=field,
        error=error,
        raw_data={"shipment_code": f"SHP-{row_number}"},
    )
    session.add(row)
    session.flush()
    return row


def add_shipment(
    session: Session,
    *,
    import_id: UUID,
    shipment_code: str,
    customer_name: str,
    origin_city: str,
    destination_city: str,
    status: str = "PENDING",
    created_at: datetime | None = None,
) -> Shipment:
    shipment = Shipment(
        id=uuid4(),
        import_id=import_id,
        shipment_code=shipment_code,
        customer_name=customer_name,
        origin_city=origin_city,
        destination_city=destination_city,
        weight_kg=Decimal("1.000"),
        price=Decimal("10.00"),
        status=status,
        delivery_date=None,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(shipment)
    session.flush()
    return shipment
