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
