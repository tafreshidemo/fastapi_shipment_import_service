from __future__ import annotations

import logging

from celery.signals import worker_ready

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.imports.services.recover_stale_imports import RecoverStaleImportsService

logger = logging.getLogger(__name__)


def recover_stale_imports_on_startup() -> int:
    """Recover stale imports before this worker starts accepting new tasks."""
    settings = get_settings()
    service = RecoverStaleImportsService(
        session_factory=get_session_factory(),
        settings=settings,
    )
    recovered = service.recover_stale_imports(batch_size=settings.startup_recovery_batch_size)
    if recovered:
        logger.info("Startup stale-import recovery completed", extra={"count": recovered})
    return recovered


@worker_ready.connect
def run_startup_recovery(**_: object) -> None:
    recover_stale_imports_on_startup()
