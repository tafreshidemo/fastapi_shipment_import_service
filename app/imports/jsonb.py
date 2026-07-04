from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def jsonb_safe(value: object) -> object:
    """Convert workbook row values into JSONB-safe primitive values."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonb_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonb_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
