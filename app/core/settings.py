from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed runtime settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Shipment Import Service"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/import_service"
    rabbitmq_url: str = "amqp://import_user:import_password@rabbitmq:5672//"
    upload_dir: Path = Path("data/uploads")
    upload_read_chunk_size_bytes: int = 1024 * 1024
    max_upload_size_bytes: int = 50 * 1024 * 1024
    processing_row_chunk_size: int = 500
    api_workers: int = 2
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800
    outbox_batch_size: int = 100
    outbox_max_attempts: int = 5
    outbox_stale_timeout_seconds: int = 60
    outbox_poll_interval_seconds: int = 1
    outbox_retry_delays_seconds: Annotated[tuple[int, ...], NoDecode] = Field(
        default=(2, 4, 8, 16)
    )
    import_max_attempts: int = 3
    processing_stale_timeout_seconds: int = 300
    watchdog_interval_seconds: int = 60
    startup_recovery_batch_size: int = 100
    watchdog_batch_size: int = 100
    celery_worker_prefetch_multiplier: int = 1
    celery_task_acks_late: bool = True
    celery_task_reject_on_worker_lost: bool = True
    default_page_size: int = 20
    max_page_size: int = 100

    @field_validator("outbox_retry_delays_seconds", mode="before")
    @classmethod
    def parse_retry_delays(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator(
        "upload_read_chunk_size_bytes",
        "max_upload_size_bytes",
        "processing_row_chunk_size",
        "api_workers",
        "db_pool_size",
        "db_pool_timeout_seconds",
        "db_pool_recycle_seconds",
        "outbox_batch_size",
        "outbox_max_attempts",
        "outbox_stale_timeout_seconds",
        "outbox_poll_interval_seconds",
        "import_max_attempts",
        "processing_stale_timeout_seconds",
        "watchdog_interval_seconds",
        "startup_recovery_batch_size",
        "watchdog_batch_size",
        "celery_worker_prefetch_multiplier",
        "default_page_size",
        "max_page_size",
    )
    @classmethod
    def validate_positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value

    @field_validator("db_max_overflow")
    @classmethod
    def validate_non_negative_integers(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be zero or a positive integer")
        return value

    @field_validator("outbox_retry_delays_seconds")
    @classmethod
    def validate_retry_delays(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != 4:
            raise ValueError("must contain exactly four retry delays")
        if any(delay <= 0 for delay in value):
            raise ValueError("retry delays must be positive integers")
        if tuple(sorted(value)) != value or len(set(value)) != len(value):
            raise ValueError("retry delays must be strictly increasing")
        return value

    @model_validator(mode="after")
    def validate_runtime_relationships(self) -> Settings:
        if self.max_upload_size_bytes < self.upload_read_chunk_size_bytes:
            raise ValueError("MAX_UPLOAD_SIZE_BYTES must be greater than upload chunk size")
        if self.max_page_size < self.default_page_size:
            raise ValueError("MAX_PAGE_SIZE must be greater than or equal to DEFAULT_PAGE_SIZE")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
