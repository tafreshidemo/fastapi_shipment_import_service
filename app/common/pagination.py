from __future__ import annotations

from dataclasses import asdict, dataclass


class InvalidPaginationError(ValueError):
    """Raised when pagination query parameters are outside API limits."""


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


@dataclass(frozen=True, slots=True)
class PaginationMetadata:
    page: int
    page_size: int
    total_items: int
    total_pages: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def parse_page_request(
    *,
    page: str | None,
    page_size: str | None,
    default_page_size: int,
    max_page_size: int,
) -> PageRequest:
    """Parse bounded pagination values from query parameters."""
    parsed_page = _parse_positive_integer(page, "page", default=1)
    parsed_page_size = _parse_positive_integer(
        page_size,
        "page_size",
        default=default_page_size,
    )
    if parsed_page_size > max_page_size:
        raise InvalidPaginationError(f"page_size must not exceed {max_page_size}.")
    return PageRequest(page=parsed_page, page_size=parsed_page_size)


def build_pagination_metadata(
    *,
    request: PageRequest,
    total_items: int,
) -> PaginationMetadata:
    total_pages = (total_items + request.page_size - 1) // request.page_size
    return PaginationMetadata(
        page=request.page,
        page_size=request.page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


def _parse_positive_integer(value: str | None, name: str, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidPaginationError(f"{name} must be a positive integer.") from exc
    if parsed <= 0:
        raise InvalidPaginationError(f"{name} must be a positive integer.")
    return parsed
