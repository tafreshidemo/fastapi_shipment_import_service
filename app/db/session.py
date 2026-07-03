from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings, get_settings


def create_engine_for_settings(settings: Settings) -> Engine:
    engine_kwargs = {
        "pool_pre_ping": True,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }
    if make_url(settings.database_url).drivername.startswith("postgresql"):
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
        )

    return create_engine(
        settings.database_url,
        **engine_kwargs,
    )


def build_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    current_settings = settings or get_settings()
    engine = create_engine_for_settings(current_settings)
    return sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return build_session_factory()
