from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportJob(Base):
    __tablename__ = "import_jobs"

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_import_jobs_idempotency_key"),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_import_jobs_status",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND failure_reason IS NOT NULL) OR "
            "(status <> 'FAILED' AND failure_reason IS NULL)",
            name="ck_import_jobs_failure_reason_terminal",
        ),
        sa.Index("ix_import_jobs_status", "status"),
        sa.Index("ix_import_jobs_status_last_heartbeat_at", "status", "last_heartbeat_at"),
        sa.Index("ix_import_jobs_idempotency_fingerprint", "idempotency_fingerprint"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        default="PENDING",
        server_default=sa.text("'PENDING'"),
    )
    original_file_name: Mapped[str] = mapped_column(String(length=255), nullable=False)
    stored_file_path: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    idempotency_fingerprint: Mapped[str] = mapped_column(String(length=64), nullable=False)
    total_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    processed_rows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa.text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    locked_by_worker: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_requeued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
