"""Canonical report-period identities shared by API and static projections."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping


def report_date(value: Any) -> date | None:
    """Return a timezone-stable calendar date for a stored report timestamp."""

    if isinstance(value, datetime):
        normalized = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return normalized.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def infer_display_cadence(values: Iterable[Any]) -> str | None:
    """Name a regular legacy date pattern without claiming a declared source period.

    Sparse or mixed date sequences stay unknown. Callers must retain the
    distinction between this display hint and source-defined period boundaries.
    """

    dates = sorted({parsed for value in values if (parsed := report_date(value))})
    if len(dates) < 4:
        return None
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    if any(gap <= 0 for gap in gaps):
        return None

    if len(dates) >= 4 and len({(day.month, day.day) for day in dates}) == 1:
        years = [day.year for day in dates]
        year_gaps = [right - left for left, right in zip(years, years[1:])]
        if year_gaps.count(1) >= 2 and all(1 <= gap <= 3 for gap in year_gaps):
            return "annual"

    if all(day.day == 1 and day.month in {1, 4, 7, 10} for day in dates):
        quarters = [day.year * 4 + (day.month - 1) // 3 for day in dates]
        quarter_gaps = [right - left for left, right in zip(quarters, quarters[1:])]
        if quarter_gaps.count(1) >= 3 and sum(1 <= gap <= 2 for gap in quarter_gaps) >= len(quarter_gaps) * 0.8:
            return "quarterly"

    if len(dates) >= 6 and all(day.day == 1 for day in dates):
        months = [day.year * 12 + day.month for day in dates]
        month_gaps = [right - left for left, right in zip(months, months[1:])]
        if month_gaps.count(1) >= 4 and sum(1 <= gap <= 3 for gap in month_gaps) >= len(month_gaps) * 0.8:
            return "monthly"

    if len(dates) >= 8 and len({day.weekday() for day in dates}) == 1:
        if gaps.count(7) >= 5 and sum(gap in {7, 14, 21, 28} for gap in gaps) >= len(gaps) * 0.8:
            return "weekly"

    if len(dates) >= 14 and gaps.count(1) >= 10:
        if sum(1 <= gap <= 3 for gap in gaps) >= len(gaps) * 0.8:
            return "daily"
    return None


def report_period_key(value: Any, temporal_granularity: str | None = None) -> str:
    """Return the source reporting-period identity used for layer overlays.

    A weekly source may encode the same surveillance week using a Saturday in
    the legacy table and a Sunday in the series registry.  Exact-date matching
    would count both.  Monthly and annual sources have the analogous issue
    when their period labels use different days within the same period.
    """

    parsed = report_date(value)
    if parsed is None:
        return str(value or "")

    granularity = str(temporal_granularity or "").strip().lower()
    if granularity == "weekly":
        iso_year, iso_week, _ = parsed.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    if granularity == "monthly":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    if granularity in {"annual", "yearly"}:
        return f"{parsed.year:04d}"
    return parsed.isoformat()


def selected_series_granularity(
    source_series: Iterable[Mapping[str, Any]],
    selected_codes: Iterable[str],
) -> str | None:
    """Resolve one safe compatibility grain for the selected source series."""

    selected = {str(code) for code in selected_codes if str(code).strip()}
    granularities = {
        str(item.get("temporal_granularity") or "").strip().lower()
        for item in source_series
        if str(item.get("series_code") or "") in selected
        and str(item.get("temporal_granularity") or "").strip()
    }
    return next(iter(granularities)) if len(granularities) == 1 else None
