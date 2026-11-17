"""Bounded DDS recovery and revision planning using verified source calendars.

A single latest timestamp cannot certify a wide disease inventory. Coverage is
kept per source code and date/place basis. Reserved revision capacity and
rotating recovery slots prevent permanently failing old reports from blocking
other updates. Explicit manual requests bypass this policy.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import select

from src.core.database import get_db
from src.domain import DiseaseSeriesObservation, DiseaseSurveillanceSeries
from src.data.processors.th_dds import DDSFetchResult


def month_start(today, months):
    index = today.year * 12 + today.month - 1 - int(months)
    year, month = divmod(index, 12)
    return date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))


async def runtime_options(updater, options, *, default_basis):
    """Load compact, actual persisted coverage only for automatic refreshes."""
    keys = ("year", "weeks", "basis", "recent_weeks", "labels")
    supplied = {key: options[key] for key in keys if options.get(key) is not None}
    if any(options.get(key) is not None for key in ("year", "weeks", "recent_weeks")):
        return supplied
    basis = options.get("basis", default_basis)
    obs, series = DiseaseSeriesObservation, DiseaseSurveillanceSeries
    async with get_db() as db:
        records = (await db.execute(select(
            obs.time, obs.raw_data["SourceDiseaseCode"].as_string(), obs.updated_at,
        ).join(series, series.series_code == obs.series_code).where(
            series.country_code == "TH", series.source_system == updater.ontology_source_id,
            obs.geography_key == "country:TH:national",
            obs.raw_data["TimeBasis"].as_string() == basis,
        ).distinct())).all()
    coverage = {}
    for when, code, updated in records:
        if not code:
            continue
        end = when.date() if isinstance(when, datetime) else when
        stamp = updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated
        key = (str(code), end.isoformat())
        # Separate case/death facts can share a period. Be conservative about
        # freshness if their writes were not simultaneous.
        coverage[key] = min(stamp, coverage.get(key, stamp))
    return {**supplied, "refresh_state": {
        "coverage": [(code, end, stamp.isoformat()) for (code, end), stamp in coverage.items()],
        "revision_window_months": int(options.get("revision_window_months") or updater.refresh_recent_months),
        "max_weeks_per_category": 4,
    }}


class RefreshPlan:
    def __init__(self, calendars, state, *, as_of, now=None):
        self.calendars = calendars
        self.as_of = as_of
        self.now = now or datetime.now(timezone.utc)
        self.months = int(state.get("revision_window_months", 3))
        self.budget = int(state.get("max_weeks_per_category", 4))
        if not 1 <= self.months <= 24 or not 4 <= self.budget <= 26:
            raise ValueError("DDS refresh requires 1..24 revision months and 4..26 weeks per category")
        self.window = month_start(as_of, self.months)
        self.coverage = {(code, end): datetime.fromisoformat(stamp) for code, end, stamp in state.get("coverage", [])}
        self.anchor = min((date.fromisoformat(end) for _, end in self.coverage), default=self.window)
        self.anchor = min(self.anchor, self.window)

    def select(self, code, eligible):
        """Return (year, week) pairs; eligible must come from official selectors."""
        periods = sorted(((self.calendars[year][week][1], year, week)
            for year, week in set(eligible)
            if self.anchor <= self.calendars[year][week][1] < self.as_of))
        if not periods:
            return []
        latest = periods[-1]
        selected = [latest]
        missing = [item for item in periods[:-1] if (code, item[0].isoformat()) not in self.coverage]
        due = [item for item in periods[:-1] if item[0] >= self.window
            and (code, item[0].isoformat()) in self.coverage
            and self._stamp(code, item[0]) <= self.now - timedelta(hours=24)]
        due.sort(key=lambda item: (self._stamp(code, item[0]), item[0]))
        # A permanently failing old report must never consume the revision
        # lane. Keep one due revision even while recovering a large outage.
        selected.extend(due[:1])
        capacity = self.budget - len(selected)
        if len(missing) <= capacity:
            selected.extend(missing)
        else:
            selected.append(missing[0])
            rotating = missing[1:]
            # A day offset prevents a daily retry at the same wall time from
            # always landing in the same modulo-48 slot. Hourly and half-hour
            # schedules also advance without persisting a success watermark.
            clock = self.now.astimezone(timezone.utc)
            slot = clock.date().toordinal() + clock.hour + clock.minute // 30
            start = slot % len(rotating)
            selected.extend(rotating[(start + offset) % len(rotating)] for offset in range(capacity - 1))
        remaining = self.budget - len(selected)
        if remaining:
            selected.extend(due[1:1 + remaining])
        return sorted((year, week) for _, year, week in selected)

    def _stamp(self, code, end):
        stamp = self.coverage[code, end.isoformat()]
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


async def calendars_for_refresh(writer, crawler, state, *, as_of, capture_calendar):
    months = int(state.get("revision_window_months", 3))
    if not 1 <= months <= 24:
        raise ValueError("DDS revision_window_months must be within 1..24")
    start = min([month_start(as_of, months), *[date.fromisoformat(row[1]) for row in state.get("coverage", [])]])
    return {year: await capture_calendar(writer, crawler, year=year)
            for year in range(start.year, as_of.year + 1)}


def merge_results(results, updater):
    if not results or not any(result.rows for result in results):
        failures = [{"year": result.source_audit.get("year", result.source_audit.get("calendar_year")), **error}
            for result in results for error in result.source_audit.get("failed_reports", [])]
        raise ValueError("No verified, published closed DDS week is available: " + json.dumps(failures, ensure_ascii=False))
    if len(results) == 1:
        return results[0]
    rows = sorted([row for result in results for row in result.rows], key=lambda row: (row["Date"], row["SourceDiseaseCode"]))
    audits = [result.source_audit for result in results]
    return DDSFetchResult(rows, max(result.source_latest_date for result in results if result.source_latest_date is not None), updater.output_csv,
        [line for result in results for line in result.script_logs], {
            "status": "partial" if any(a["status"] == "partial" for a in audits) else "validated",
            "source_scope": updater.source_scope, "observations": len(rows),
            "requested_reports": sum(a["requested_reports"] for a in audits),
            "failed_reports": [{"year": a.get("year", a.get("calendar_year")), **error}
                for a in audits for error in a["failed_reports"]],
            "year_audits": audits, "public_release_enabled": False, "authoritative_replacement": False,
        })
