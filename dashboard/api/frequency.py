"""Helpers for inferring and working with dashboard time frequencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.disease_record import DiseaseRecord

PeriodUnit = Literal["week", "biweek", "month"]
TimeBucket = Literal["week", "month"]


@dataclass(frozen=True)
class FrequencyProfile:
    period_unit: PeriodUnit
    bucket: TimeBucket
    cadence_days: int


WEEKLY_PROFILE = FrequencyProfile(period_unit="week", bucket="week", cadence_days=7)
BIWEEKLY_PROFILE = FrequencyProfile(period_unit="biweek", bucket="week", cadence_days=14)
MONTHLY_PROFILE = FrequencyProfile(period_unit="month", bucket="month", cadence_days=30)
_CANDIDATE_PROFILES: tuple[FrequencyProfile, ...] = (
    WEEKLY_PROFILE,
    BIWEEKLY_PROFILE,
    MONTHLY_PROFILE,
)


def _normalize_distinct_times(times: Iterable[datetime]) -> list[datetime]:
    seen: set[datetime] = set()
    ordered: list[datetime] = []
    for ts in sorted(t for t in times if t is not None):
        if ts in seen:
            continue
        seen.add(ts)
        ordered.append(ts)
    return ordered


def _positive_day_gaps(times: list[datetime]) -> list[int]:
    gaps: list[int] = []
    for previous, current in zip(times, times[1:]):
        gap = abs((current - previous).days)
        if gap > 0:
            gaps.append(gap)
    return gaps


def _candidate_tolerance_days(profile: FrequencyProfile) -> int:
    if profile.period_unit == "month":
        return 5
    return 2


def infer_frequency_profile_from_times(times: Iterable[datetime]) -> FrequencyProfile:
    """
    Infer cadence from timestamps using candidate-fit scoring.

    Candidates are:
    - weekly (7 days)
    - biweekly (14 days)
    - monthly (~30 days)

    The scorer favors candidates that:
    - explain most gaps as near-integer multiples of the cadence
    - explain more gaps as exactly one cadence step
    - keep residual day error small
    """
    ordered_times = _normalize_distinct_times(times)
    if len(ordered_times) < 2:
        return MONTHLY_PROFILE

    gaps = _positive_day_gaps(ordered_times)
    if not gaps:
        return MONTHLY_PROFILE

    best_profile = MONTHLY_PROFILE
    best_score = float("-inf")

    for profile in _CANDIDATE_PROFILES:
        tolerance = _candidate_tolerance_days(profile)
        fits = 0
        exact_steps = 0
        errors: list[int] = []

        for gap in gaps:
            step_multiple = max(1, round(gap / profile.cadence_days))
            expected_gap = step_multiple * profile.cadence_days
            error = abs(gap - expected_gap)
            errors.append(error)
            if error <= tolerance:
                fits += 1
                if step_multiple == 1:
                    exact_steps += 1

        fit_ratio = fits / len(gaps)
        exact_ratio = exact_steps / len(gaps)
        median_error = median(errors) if errors else 999

        score = (fit_ratio * 100.0) + (exact_ratio * 25.0) - (median_error * 3.0)

        if score > best_score:
            best_score = score
            best_profile = profile

    return best_profile


async def infer_country_frequency_profile(
    country_id: int,
    db: AsyncSession,
) -> FrequencyProfile:
    """Infer the dominant cadence for a country using recent distinct timestamps."""
    times_q = (
        select(DiseaseRecord.time)
        .where(DiseaseRecord.country_id == country_id)
        .distinct()
        .order_by(DiseaseRecord.time.desc())
        .limit(24)
    )
    times = [row[0] for row in (await db.execute(times_q)).all()]
    return infer_frequency_profile_from_times(times)


async def infer_country_frequency(country_id: int, db: AsyncSession) -> TimeBucket:
    """Keep overview-style week/month grouping while using smarter profile inference."""
    profile = await infer_country_frequency_profile(country_id, db)
    return profile.bucket


def period_start(ts: datetime, profile: FrequencyProfile) -> datetime:
    """Return the normalized period start for a timestamp under the given profile."""
    current = ts.astimezone(timezone.utc) if ts.tzinfo else ts
    normalized = current.replace(hour=0, minute=0, second=0, microsecond=0)

    if profile.period_unit == "month":
        return normalized.replace(day=1)

    anchor = datetime(1970, 1, 5, tzinfo=normalized.tzinfo)
    offset_days = (normalized - anchor).days
    span_days = profile.cadence_days
    return anchor + timedelta(days=(offset_days // span_days) * span_days)


def expected_periods(
    start_period: datetime | None,
    end_period: datetime | None,
    profile: FrequencyProfile,
) -> int:
    """Count inclusive expected periods between two normalized period starts."""
    if start_period is None or end_period is None:
        return 0

    if start_period > end_period:
        start_period, end_period = end_period, start_period

    if profile.period_unit == "month":
        return ((end_period.year - start_period.year) * 12) + (end_period.month - start_period.month) + 1

    return int((end_period - start_period).days // profile.cadence_days) + 1


def period_gap(
    start_period: datetime | None,
    next_period: datetime | None,
    profile: FrequencyProfile,
) -> int:
    """Return the number of inferred periods between two adjacent observed starts."""
    if start_period is None or next_period is None:
        return 0

    if next_period < start_period:
        start_period, next_period = next_period, start_period

    if profile.period_unit == "month":
        return ((next_period.year - start_period.year) * 12) + (next_period.month - start_period.month)

    return int((next_period - start_period).days // profile.cadence_days)
