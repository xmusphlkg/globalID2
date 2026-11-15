"""Keep SQL statements under PostgreSQL's 32767-parameter limit."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

QUERY_ARGUMENT_LIMIT = 5000

T = TypeVar("T")


def argument_batches(values: Iterable[T], size: int = QUERY_ARGUMENT_LIMIT) -> Iterator[list[T]]:
    """Yield bind-sized chunks. An empty input yields nothing."""
    if size < 1:
        raise ValueError("query batch size must be positive")
    items = list(values)
    for offset in range(0, len(items), size):
        yield items[offset:offset + size]
