from __future__ import annotations

from app.celery_app import celery_app
from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.imports.services.recover_stale_imports import RecoverStaleImportsService


@celery_app.task(name="imports.recover_stale_imports")
def recover_stale_imports_task() -> int:
    """Run the bounded watchdog recovery from Celery Beat."""
    settings = get_settings()
    service = RecoverStaleImportsService(
        session_factory=get_session_factory(),
        settings=settings,
    )
    return service.recover_stale_imports(batch_size=settings.watchdog_batch_size)
