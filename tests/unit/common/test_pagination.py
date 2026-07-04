from __future__ import annotations

import pytest

from app.common.pagination import (
    InvalidPaginationError,
    PageRequest,
    build_pagination_metadata,
    parse_page_request,
)


def test_parse_page_request_uses_defaults_and_enforces_bounds() -> None:
    request = parse_page_request(
        page=None,
        page_size=None,
        default_page_size=20,
        max_page_size=100,
    )

    assert request == PageRequest(page=1, page_size=20)
    assert request.offset == 0
    assert build_pagination_metadata(request=request, total_items=41).as_dict() == {
        "page": 1,
        "page_size": 20,
        "total_items": 41,
        "total_pages": 3,
    }


@pytest.mark.parametrize(
    ("page", "page_size"),
    [
        ("0", None),
        ("-1", None),
        ("not-a-number", None),
        (None, "0"),
        (None, "101"),
    ],
)
def test_parse_page_request_rejects_invalid_values(page: str | None, page_size: str | None) -> None:
    with pytest.raises(InvalidPaginationError):
        parse_page_request(
            page=page,
            page_size=page_size,
            default_page_size=20,
            max_page_size=100,
        )
