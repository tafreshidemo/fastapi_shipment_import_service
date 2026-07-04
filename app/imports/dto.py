from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ImportCreatedResult:
    import_id: UUID
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportStatusReadModel:
    import_id: UUID
    status: str
    total_rows: int
    processed_rows: int
    success_count: int
    failed_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_failure_reason: str | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class ImportErrorReadModel:
    row_number: int
    field: str
    error: str
