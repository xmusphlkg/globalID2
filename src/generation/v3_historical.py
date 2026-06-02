"""Historical context helpers for analytical v3 evidence packets."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from src.generation.data_cleaner import Frequency


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return round(((current - previous) / previous) * 100.0, 2)


def build_historical_context(wide: pd.DataFrame, frequency: Frequency) -> Dict[str, Any]:
    """Summarize the latest signal against the available historical record."""
    if wide.empty or "cases" not in wide.columns:
        return {
            "observation_count": 0,
            "interpretation": "No historical context is available.",
        }

    values = pd.to_numeric(wide.get("cases", pd.Series(dtype=float)), errors="coerce").fillna(0).reset_index(drop=True)
    periods = wide.get("_period_str", pd.Series([str(i) for i in range(len(wide))])).reset_index(drop=True)
    period_dates = pd.to_datetime(wide.get("_period", pd.Series(dtype="datetime64[ns]")), errors="coerce")
    latest = float(values.iloc[-1]) if len(values) else 0.0
    prior = values.iloc[:-1]
    total_cases = float(values.sum())
    max_cases = float(values.max()) if len(values) else 0.0
    latest_rank = int((values > latest).sum() + 1) if len(values) else None
    latest_percentile = None
    if len(prior) > 0:
        latest_percentile = round(float((prior <= latest).mean() * 100.0), 1)

    horizon = {"daily": 30, "weekly": 13, "monthly": 12}.get(str(frequency), 12)
    horizon = min(horizon, max(1, len(values)))
    recent_window = float(values.tail(horizon).sum()) if horizon else 0.0
    previous_window = (
        float(values.iloc[-2 * horizon : -horizon].sum())
        if len(values) >= horizon * 2
        else None
    )
    window_change = _pct_change(recent_window, previous_window)
    recent_share = round((recent_window / total_cases) * 100.0, 2) if total_cases > 0 else 0.0

    periods_since_peak = None
    peak_period = None
    if len(values) > 0:
        peak_position = int(values.idxmax())
        periods_since_peak = int(len(values) - peak_position - 1)
        peak_period = str(periods.iloc[peak_position])

    seasonal_median = None
    seasonal_count = 0
    latest_to_seasonal_median_ratio = None
    if len(period_dates) == len(values) and period_dates.notna().any():
        latest_date = pd.Timestamp(period_dates.iloc[-1])
        comparable_mask = pd.Series([False] * len(values))
        if frequency == "monthly":
            comparable_mask = period_dates.dt.month == latest_date.month
        elif frequency == "weekly":
            latest_week = latest_date.isocalendar().week
            comparable_mask = period_dates.dt.isocalendar().week.astype(int) == int(latest_week)
        elif frequency == "daily":
            comparable_mask = (period_dates.dt.month == latest_date.month) & (period_dates.dt.day == latest_date.day)
        comparable = values.iloc[:-1][comparable_mask.iloc[:-1].to_numpy()]
        seasonal_count = int(len(comparable))
        if seasonal_count:
            seasonal_median = round(float(comparable.median()), 2)
            if seasonal_median > 0:
                latest_to_seasonal_median_ratio = round(latest / seasonal_median, 3)

    latest_to_max_ratio = round(latest / max_cases, 3) if max_cases > 0 else None
    interpretation_parts: List[str] = []
    if latest_percentile is not None:
        interpretation_parts.append(f"Latest observation is at the {latest_percentile:.1f}th percentile of prior observations.")
    if window_change is not None:
        interpretation_parts.append(f"Last {horizon} periods changed {window_change:+.1f}% versus the preceding matched window.")
    if latest_to_seasonal_median_ratio is not None:
        interpretation_parts.append(f"Latest observation is {latest_to_seasonal_median_ratio:.2f}x the same-season historical median.")
    if not interpretation_parts:
        interpretation_parts.append("Historical comparison is limited by short series length.")

    return {
        "observation_count": int(len(values)),
        "available_period_start": str(periods.iloc[0]) if len(periods) else None,
        "available_period_end": str(periods.iloc[-1]) if len(periods) else None,
        "latest_percentile_prior": latest_percentile,
        "latest_rank_by_cases": latest_rank,
        "historical_max_cases": int(max_cases) if max_cases.is_integer() else round(max_cases, 2),
        "historical_peak_period": peak_period,
        "periods_since_peak": periods_since_peak,
        "latest_to_historical_max_ratio": latest_to_max_ratio,
        "long_window_periods": int(horizon),
        "long_window_cases": int(recent_window),
        "previous_long_window_cases": int(previous_window) if previous_window is not None else None,
        "long_window_change_pct": window_change,
        "long_window_share_pct": recent_share,
        "same_season_baseline_count": seasonal_count,
        "same_season_median_cases": seasonal_median,
        "latest_to_same_season_median_ratio": latest_to_seasonal_median_ratio,
        "interpretation": " ".join(interpretation_parts),
    }
