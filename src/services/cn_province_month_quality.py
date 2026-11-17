"""Evidence-only checks for suspicious provincial month assignments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


def _month(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:7] + "-01")
    except (TypeError, ValueError):
        return None


def _case_values(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[date, str], Decimal]:
    values: dict[tuple[date, str], Decimal] = {}
    for row in rows:
        series = str(row.get("series_code") or "")
        if not series.startswith("SER_CN_PROV_REPORT_") or series.startswith("SER_CN_PROV_REPORT_DEATHS_"):
            continue
        month = _month(row.get("time"))
        if month is None or row.get("value") is None or isinstance(row["value"], bool):
            continue
        try:
            value = Decimal(str(row["value"]))
        except InvalidOperation:
            continue
        if not value.is_finite() or value < 0 or value != value.to_integral_value():
            continue
        key = (month, series)
        if key in values and values[key] != value:
            return {}
        values[key] = value
    return values


def detect_previous_month_copy(
    current_rows: Iterable[Mapping[str, Any]],
    previous_rows: Iterable[Mapping[str, Any]],
    *,
    min_value: int = 100,
    min_matches: int = 4,
) -> dict[str, Any] | None:
    """Flag a cluster of identical large case counts in consecutive months.

    Inputs are one province's candidate observations and its preceding month's
    stored observations. A finding requires review of the original reports;
    it does not prove that either month's published values are wrong.
    """
    if min_value < 1 or min_matches < 2:
        raise ValueError("Previous-month copy thresholds must be positive and require two or more matches")
    current = _case_values(current_rows)
    previous = _case_values(previous_rows)
    current_months = {month for month, _ in current}
    previous_months = {month for month, _ in previous}
    if len(current_months) != 1 or len(previous_months) != 1:
        return None
    current_month = current_months.pop()
    previous_month = previous_months.pop()
    if (current_month - timedelta(days=1)).replace(day=1) != previous_month:
        return None
    matches = [
        {"series_code": series, "value": int(value)}
        for (month, series), value in current.items()
        if month == current_month and value >= min_value
        and previous.get((previous_month, series)) == value
    ]
    if len(matches) < min_matches:
        return None
    return {
        "reason": "suspected_previous_month_copy",
        "current_month": current_month.strftime("%Y-%m"),
        "previous_month": previous_month.strftime("%Y-%m"),
        "matched_count": len(matches),
        "matches": sorted(matches, key=lambda item: item["series_code"]),
    }


__all__ = ["detect_previous_month_copy"]
