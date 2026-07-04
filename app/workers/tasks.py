from __future__ import annotations

from uuid import UUID

from app.celery_app import celery_app
from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.imports.services.process_import import (
    ProcessImportService,
    RetryableImportProcessingError,
)


@celery_app.task(bind=True, name="imports.process_import")
def process_import_task(self, import_id: str) -> None:
    """Create worker-local dependencies and delegate processing to the service."""
    settings = get_settings()
    service = ProcessImportService(
        session_factory=get_session_factory(),
        settings=settings,
        worker_id=self.request.hostname or "celery-worker",
    )
    try:
        service.run(UUID(import_id))
    except RetryableImportProcessingError as exc:
        raise self.retry(
            exc=exc,
            countdown=2 ** self.request.retries,
            max_retries=settings.import_max_attempts - 1,
        ) from exc
