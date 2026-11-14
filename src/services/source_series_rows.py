"""Source-row interpretation for component rebuilds; missing values never become zero."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from pathlib import Path
import unicodedata
from typing import Any, Iterable

_SUPPRESSED_VALUES = frozenset({"*", "suppressed", "suppression", "<5", "-"})

@dataclass(frozen=True)
class SourceAdapter:
    country_code: str
    source_id: str
    paths: tuple[Path, ...]
    label_fields: tuple[str, ...]
    value_field: str
    national_fields: tuple[str, ...] = ()
    national_values: tuple[str, ...] = ()
    week_calendar: str | None = None
    blocked_reason: str | None = None

def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).casefold()

def _first_text(row: dict[str, str], fields: Iterable[str]) -> str | None:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return None

def _value_kind(value: object) -> tuple[str, float | None]:
    text = str(value or "").strip()
    if not text:
        return "missing", None
    if text.casefold() in _SUPPRESSED_VALUES:
        return "suppressed", None
    try:
        numeric = float(text.replace(",", ""))
    except ValueError:
        return "invalid", None
    if not math.isfinite(numeric):
        return "invalid", None
    return ("zero" if numeric == 0 else "nonzero"), numeric

def _is_national(row: dict[str, str], adapter: SourceAdapter) -> bool:
    if not adapter.national_fields:
        return True
    value = _first_text(row, adapter.national_fields)
    return value is not None and _normalize(value) in {
        _normalize(item) for item in adapter.national_values
    }

def _row_identity(row: dict[str, str], label: str) -> tuple[str, ...]:
    """Natural source identity used only to de-duplicate overlapping extracts."""

    # Every adapter in this command has already restricted rows to the national
    # geography and every call is scoped to exactly one Registry series. Source
    # label aliases (and spelling changes such as US RESIDENTS/U.S. RESIDENTS)
    # must therefore collapse to the same period identity. If their values do
    # not agree, the caller records a conflict instead of silently selecting one.
    del label
    return (
        str(row.get("Date") or row.get("date") or "").strip(),
        str(row.get("Current MMWR Year") or row.get("MMWRYear") or "").strip(),
        str(row.get("MMWR WEEK") or row.get("MMWRWeek") or "").strip(),
    )

def _mmwr_week_end(year: int, week: int) -> date:
    """Return the CDC MMWR Saturday for a surveillance year/week."""

    if not 1 <= week <= 53:
        raise ValueError("MMWR week must be between 1 and 53")
    january_fourth = date(year, 1, 4)
    # Python Monday=0; the Sunday on or before Jan 4 starts MMWR week 1.
    week_one_start = january_fourth - timedelta(days=(january_fourth.weekday() + 1) % 7)
    following_january_fourth = date(year + 1, 1, 4)
    following_week_one_start = following_january_fourth - timedelta(
        days=(following_january_fourth.weekday() + 1) % 7
    )
    week_start = week_one_start + timedelta(weeks=week - 1)
    if week_start >= following_week_one_start:
        raise ValueError(f"MMWR year {year} does not contain week {week}")
    return week_start + timedelta(days=6)

def _prepare_row(
    row: dict[str, str], adapter: SourceAdapter, label: str, path: Path
) -> dict[str, str]:
    prepared = dict(row)
    prepared["RawDiseaseLabel"] = label
    prepared["__source_file"] = str(path)
    if (
        adapter.source_id == "SRC_US_NNDSS"
        and not str(prepared.get("IsProvisional") or "").strip()
    ):
        # Weekly NNDSS extracts are provisional surveillance notifications.
        # Preserve that quality rank so a component rebuild cannot downgrade
        # an existing provisional observation to an unqualified raw value.
        prepared["IsProvisional"] = "true"
    if adapter.week_calendar == "mmwr_saturday":
        try:
            year = int(float(str(row.get("Current MMWR Year") or row.get("MMWRYear"))))
            week = int(float(str(row.get("MMWR WEEK") or row.get("MMWRWeek"))))
            prepared["Date"] = _mmwr_week_end(year, week).isoformat()
        except (TypeError, ValueError):
            # Leave Date absent so SeriesObservationStore classifies the row as
            # invalid rather than inventing a period.
            pass
    return prepared

def _source_file_stat(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "matched_rows": 0,
        "non_empty": 0,
        "nonzero": 0,
        "zero": 0,
        "missing": 0,
        "suppressed": 0,
        "invalid": 0,
    }

def _time_coverage(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not observations:
        return None
    values: list[datetime] = sorted(row["time"] for row in observations)
    return {
        "start": values[0].isoformat(),
        "end": values[-1].isoformat(),
        "observation_count": len(values),
        "distinct_periods": len(set(values)),
    }
