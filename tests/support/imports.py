from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.db.models.import_job import ImportJob

WORKBOOK_HEADERS = [
    "shipment_code",
    "customer_name",
    "origin_city",
    "destination_city",
    "weight_kg",
    "price",
    "status",
    "delivery_date",
]


def write_workbook(
    path: Path,
    rows: list[list[object | None]],
    *,
    headers: list[str] | None = None,
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers or WORKBOOK_HEADERS)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def create_import_job(
    session: Session,
    *,
    workbook_path: Path,
    import_id: UUID | None = None,
    status: str = "PENDING",
    attempt_count: int = 0,
) -> ImportJob:
    job = ImportJob(
        id=import_id or uuid4(),
        status=status,
        original_file_name=workbook_path.name,
        stored_file_path=str(workbook_path),
        file_size_bytes=workbook_path.stat().st_size,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        idempotency_key=None,
        idempotency_fingerprint=uuid4().hex + uuid4().hex,
        attempt_count=attempt_count,
        max_attempts=3,
    )
    session.add(job)
    session.flush()
    return job
