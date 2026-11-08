"""Stable calendar windows for downloadable time series."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SPECIAL_WINDOW_START = 2020
SPECIAL_WINDOW_END = 2025
BRIDGE_WINDOW_START = 2026
BRIDGE_WINDOW_END = 2029
ROLLING_WINDOW_START = 2030
ROLLING_WINDOW_YEARS = 5


@dataclass(frozen=True)
class PartitionSpec:
    """One stable calendar window used as a public file name."""

    key: str
    start: date
    end: date
    label: str
    is_current: bool = False


def _window_for_year(year: int, latest_year: int) -> PartitionSpec:
    if year < SPECIAL_WINDOW_START:
        start_year = year - (year % 5)
        end_year = start_year + 4
    elif year <= SPECIAL_WINDOW_END:
        start_year = SPECIAL_WINDOW_START
        end_year = SPECIAL_WINDOW_END
    elif year <= BRIDGE_WINDOW_END:
        start_year = BRIDGE_WINDOW_START
        end_year = BRIDGE_WINDOW_END
    else:
        start_year = year - (year % ROLLING_WINDOW_YEARS)
        end_year = start_year + ROLLING_WINDOW_YEARS - 1

    current = start_year <= latest_year <= end_year
    label = f"{start_year}–now" if current else f"{start_year}–{end_year}"
    return PartitionSpec(
        key=f"{start_year}-{end_year}",
        start=date(start_year, 1, 1),
        end=date(end_year, 12, 31),
        label=label,
        is_current=current,
    )


def partition_rows(rows: list[dict]) -> list[tuple[PartitionSpec, list[dict]]]:
    """Group rows into stable historical windows and one current window."""

    valid_rows = [row for row in rows if str(row.get("date") or "")[:4].isdigit()]
    if not valid_rows:
        return []
    latest_year = max(int(str(row["date"])[:4]) for row in valid_rows)
    grouped: dict[str, tuple[PartitionSpec, list[dict]]] = {}
    for row in valid_rows:
        spec = _window_for_year(int(str(row["date"])[:4]), latest_year)
        grouped.setdefault(spec.key, (spec, []))[1].append(row)
    return sorted(grouped.values(), key=lambda item: item[0].start, reverse=True)
