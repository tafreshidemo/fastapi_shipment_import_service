from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.v1.runtime import router as runtime_router
from app.core.logging import configure_logging
from app.core.settings import get_settings


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(title=settings.app_name)
    application.include_router(runtime_router, prefix="/api/v1")
    register_exception_handlers(application)
    return application
