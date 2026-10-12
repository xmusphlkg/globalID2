"""Freeze the observations used for a report's surveillance figures."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from typing import Any

import pandas as pd

from src.generation.data_cleaner import Frequency, long_to_wide, resolve_frequency

from .models import METHOD_VERSION

SNAPSHOT_SCHEMA = "report_surveillance.v1"


def _number(value: Any) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _values(wide: pd.DataFrame, column: str) -> list[int | float | None]:
    return (
        [_number(value) for value in wide[column]]
        if column in wide
        else [None] * len(wide)
    )


def _end_exclusive(start: Any, grain: Frequency) -> str:
    date = pd.Timestamp(start)
    if grain in {"monthly", "quarterly", "annual"}:
        end = date + pd.DateOffset(
            months={"monthly": 1, "quarterly": 3, "annual": 12}[grain]
        )
    else:
        end = date + pd.Timedelta(days=7 if grain == "weekly" else 1)
    return end.strftime("%Y-%m-%d")


def build_surveillance_snapshot(
    data: pd.DataFrame,
    *,
    diseases: dict[int, dict[str, Any]],
    period_start,
    period_end,
) -> dict[str, Any]:
    """Use the same period aggregation as report evidence, without a 52-point cap.

    The payload is stored with the report and never rebuilt from current site data.
    Missing observations remain null, including unavailable death counts.
    """
    frame = data.copy()
    if frame.empty or "time" not in frame or "disease_id" not in frame:
        frame = pd.DataFrame(columns=["time", "disease_id"])
    else:
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
        frame = frame[
            frame["time"].dt.date.between(
                pd.Timestamp(period_start).date(), pd.Timestamp(period_end).date()
            )
            & frame["disease_id"].isin(diseases)
        ]

    report_start = pd.Timestamp(period_start).date()
    report_end_exclusive = pd.Timestamp(period_end).date() + timedelta(days=1)
    series: dict[str, dict[str, Any]] = {}
    for disease_id, group in frame.groupby("disease_id"):
        meta = diseases.get(int(disease_id)) or {}
        code = str(meta.get("code") or meta.get("name") or disease_id)
        grain = resolve_frequency(group)
        wide = long_to_wide(group, grain or "daily", time_col="time")
        if wide.empty:
            continue
        dates = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in wide["_period"]]
        period_ends = (
            [_end_exclusive(value, grain) for value in wide["_period"]]
            if grain
            else [None] * len(dates)
        )
        statuses = [
            "partial_report_window"
            if grain
            and (
                pd.Timestamp(start).date() < report_start
                or pd.Timestamp(end).date() > report_end_exclusive
            )
            else ""
            for start, end in zip(dates, period_ends)
        ]
        cases = _values(wide, "cases")
        deaths = _values(wide, "deaths")
        rates = _values(wide, "incidence_rate")
        series[code] = {
            "disease_id": code,
            "name_en": meta.get("name_en") or code,
            "name_zh": meta.get("name_zh") or meta.get("name_en") or code,
            "category": meta.get("category"),
            "dates": dates,
            "period_starts": dates if grain else [None] * len(dates),
            "period_ends_exclusive": period_ends,
            "point_quality_statuses": statuses,
            "calendar": "iso-week"
            if grain == "weekly"
            else "gregorian"
            if grain
            else "unknown",
            "cases": cases,
            "deaths": deaths,
            "incidence_rates": rates,
            "weekly_equiv_cases": [],
            "total_cases": sum(value for value in cases if value is not None),
            "total_deaths": sum(value for value in deaths if value is not None)
            if any(value is not None for value in deaths)
            else None,
            "period_granularity": grain or "unknown",
            "metric_type": "case_notifications",
            "comparability": "unknown",
            "data_layer": "report_snapshot",
            "projection_policy": "report_evidence_period_aggregation",
        }
    payload = {
        "schema_version": SNAPSHOT_SCHEMA,
        "method_version": METHOD_VERSION,
        "period_start": str(pd.Timestamp(period_start).date()),
        "period_end": str(pd.Timestamp(period_end).date()),
        "series": series,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {**payload, "version": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def ensure_snapshot_matches_evidence(
    snapshot: dict[str, Any], packet: dict[str, Any]
) -> None:
    """Do not publish a frozen curve whose burden disagrees with the report."""
    for disease in packet.get("diseases") or []:
        code = str(disease.get("disease_id") or "")
        expected = (disease.get("metrics") or {}).get("total_cases")
        archived = (snapshot.get("series") or {}).get(code)
        if not archived or expected is None or archived.get("total_cases") != expected:
            raise ValueError(
                f"Surveillance snapshot disagrees with report evidence: {code}"
            )
