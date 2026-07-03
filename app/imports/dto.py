from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ImportCreatedResult:
    import_id: UUID
    status: str
    created_at: datetime
