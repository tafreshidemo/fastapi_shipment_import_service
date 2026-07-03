from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Shipment(Base):
    __tablename__ = "shipments"

    __table_args__ = (
        UniqueConstraint("shipment_code", name="uq_shipments_shipment_code"),
        CheckConstraint(
            "status IN ('PENDING', 'IN_TRANSIT', 'DELIVERED', 'CANCELED')",
            name="ck_shipments_status",
        ),
        CheckConstraint("weight_kg > 0", name="ck_shipments_weight_kg_positive"),
        CheckConstraint("price >= 0", name="ck_shipments_price_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    import_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    shipment_code: Mapped[str] = mapped_column(String(length=128), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(length=150), nullable=False)
    origin_city: Mapped[str] = mapped_column(String(length=255), nullable=False)
    destination_city: Mapped[str] = mapped_column(String(length=255), nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(length=32),
        nullable=False,
        default="PENDING",
        server_default=sa.text("'PENDING'"),
    )
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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
