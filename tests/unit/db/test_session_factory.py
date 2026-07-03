import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.db import session as session_module


def test_session_factory_uses_sync_session_configuration() -> None:
    settings = Settings(database_url="postgresql+psycopg://postgres:postgres@127.0.0.1:54329/import_service")

    session_factory = session_module.build_session_factory(settings)

    assert issubclass(session_factory.class_, Session)
    assert session_factory.kw["expire_on_commit"] is False
    assert session_factory.kw["autoflush"] is False


def test_async_session_is_not_defined() -> None:
    assert not hasattr(session_module, "AsyncSession")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("upload_read_chunk_size_bytes", 0),
        ("max_upload_size_bytes", 0),
        ("processing_row_chunk_size", 0),
        ("api_workers", 0),
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
    ],
)
def test_settings_reject_invalid_runtime_integers(field_name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: value})


def test_settings_reject_invalid_retry_delay_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(outbox_retry_delays_seconds="2,4,8")

    with pytest.raises(ValidationError):
        Settings(outbox_retry_delays_seconds="2,4,4,16")


def test_settings_reject_invalid_runtime_relationships() -> None:
    with pytest.raises(ValidationError):
        Settings(upload_read_chunk_size_bytes=1024, max_upload_size_bytes=512)

    with pytest.raises(ValidationError):
        Settings(default_page_size=50, max_page_size=20)
