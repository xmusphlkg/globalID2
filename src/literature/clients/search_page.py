"""A single bounded discovery page and its durable continuation."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchPage:
    records: list[dict[str, Any]]
    next_cursor: str | int | None = None
    split_required: bool = False


def cursor_page(
    records: list[dict[str, Any]],
    *,
    size: int,
    cursor: str,
    next_cursor: Any,
    missing_cursor_is_terminal: bool = False,
) -> SearchPage:
    """Accept terminal pages; reject oversized pages and non-advancing cursors.

    Crossref documents an absent next cursor as the end of the result set,
    including when the last page happens to fill the requested size.
    """
    if len(records) > size:
        raise ValueError("Discovery provider exceeded the requested page size")
    if len(records) < size:
        return SearchPage(records)
    token = str(next_cursor or "")
    if not token and missing_cursor_is_terminal:
        return SearchPage(records)
    if not token or token == cursor:
        raise ValueError("Full discovery page has no advancing cursor")
    return SearchPage(records, next_cursor=token)
