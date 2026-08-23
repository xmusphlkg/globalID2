"""Gold-standard event-label utilities for Situation Room v3.2.

The functions in this module are deliberately pure.  They are shared by the
offline replay/calibration workflow and tests, while persistence remains in
``persistence.py``.  Missing official events never manufacture negative labels.
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .contracts import SituationSignalV3


VALID_SPLITS = {"development", "tuning", "locked_test", "unassigned"}


@dataclass(frozen=True)
class EventLabelMatch:
    label_id: str
    relation: str
    period_distance: int
    reference_date: date
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def authoritative_source_url(url: str, allowed_domains: Iterable[str]) -> bool:
    parsed = urlparse(str(url or "").strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False
    domains = {
        str(domain).lower().strip().lstrip(".").rstrip(".")
        for domain in allowed_domains
        if str(domain).strip()
    }
    return bool(domains) and any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in domains
    )


def period_distance(left: date, right: date, cadence: str) -> int:
    """Return signed source periods from ``right`` to ``left``."""

    if cadence == "monthly":
        return (left.year - right.year) * 12 + left.month - right.month
    if cadence == "weekly":
        return int(round((left - right).days / 7.0))
    if cadence == "daily":
        return (left - right).days
    raise ValueError(f"unsupported cadence: {cadence}")


def _label_geography_codes(label: Any) -> set[str]:
    codes: set[str] = set()
    for geography in _field(label, "geographies", []) or []:
        if isinstance(geography, Mapping):
            for key in ("code", "country_code", "canonical_geography_key"):
                value = geography.get(key)
                if value:
                    codes.add(str(value).strip().upper())
        elif geography:
            codes.add(str(geography).strip().upper())
    return codes


def _signal_geography_codes(signal: SituationSignalV3) -> set[str]:
    identity = signal.identity
    values = {
        identity.country_code,
        identity.canonical_geography_key,
        *identity.source_geography_keys,
    }
    return {str(value).strip().upper() for value in values if value}


def match_signal_to_labels(
    signal: SituationSignalV3,
    labels: Iterable[Any],
    *,
    maximum_period_distance: int = 2,
) -> list[EventLabelMatch]:
    """Match only adjudicated positives by disease, geography, and time.

    ``lead`` means that the statistical signal predates the first official
    publication, not merely that it occurred before an event record was loaded.
    """

    if maximum_period_distance < 0:
        raise ValueError("maximum_period_distance must be non-negative")
    signal_date = signal.observation.data_through
    signal_geographies = _signal_geography_codes(signal)
    matches: list[EventLabelMatch] = []
    for label in labels:
        if str(_field(label, "adjudication", "indeterminate")) != "positive":
            continue
        if str(_field(label, "disease_id", "")) != signal.identity.disease_id:
            continue
        label_geographies = _label_geography_codes(label)
        if not label_geographies or not signal_geographies.intersection(label_geographies):
            continue
        reference_date = _as_date(_field(label, "first_official_published_at"))
        # Signed as signal minus official publication: negative is genuinely early.
        distance = period_distance(signal_date, reference_date, signal.identity.cadence)
        if abs(distance) > maximum_period_distance:
            continue
        relation = "lead" if distance < 0 else "lag" if distance > 0 else "concurrent"
        matches.append(
            EventLabelMatch(
                label_id=str(_field(label, "label_id")),
                relation=relation,
                period_distance=distance,
                reference_date=reference_date,
                source_url=str(_field(label, "source_url", "")),
            )
        )
    return sorted(matches, key=lambda item: (abs(item.period_distance), item.label_id))


def assign_temporal_splits(
    labels: Iterable[Any],
    *,
    cadence: str,
    embargo_periods: int = 2,
) -> dict[str, str]:
    """Assign chronological 70/15/15 splits with an embargo at both boundaries."""

    if embargo_periods < 0:
        raise ValueError("embargo_periods must be non-negative")
    ordered = sorted(
        [
            (
                str(_field(label, "label_id")),
                _as_date(_field(label, "first_official_published_at")),
            )
            for label in labels
        ],
        key=lambda item: (item[1], item[0]),
    )
    if not ordered:
        return {}
    count = len(ordered)
    development_count = max(1, int(count * 0.70))
    tuning_end = max(development_count + 1, int(count * 0.85))
    development_count = min(development_count, count)
    tuning_end = min(tuning_end, count)

    assignments: dict[str, str] = {}
    for index, (label_id, _) in enumerate(ordered):
        assignments[label_id] = (
            "development"
            if index < development_count
            else "tuning"
            if index < tuning_end
            else "locked_test"
        )

    boundaries = []
    if development_count < count:
        boundaries.append(ordered[development_count][1])
    if tuning_end < count:
        boundaries.append(ordered[tuning_end][1])
    for label_id, published_at in ordered:
        if any(
            abs(period_distance(published_at, boundary, cadence)) <= embargo_periods
            for boundary in boundaries
        ):
            assignments[label_id] = "unassigned"
    return assignments


def label_from_official_event(
    event: Mapping[str, Any],
    *,
    allowed_domains: Iterable[str],
) -> dict[str, Any] | None:
    """Normalize an authoritative published event into a positive label candidate."""

    disease_id = str(event.get("disease_id") or "").strip()
    source_url = str(event.get("source_url") or "").strip()
    published_at = event.get("published_at")
    geographies = event.get("geographies") or []
    if (
        not disease_id
        or not published_at
        or not geographies
        or (
            event.get("status") is not None
            and str(event.get("status")).lower() != "published"
        )
        or not authoritative_source_url(source_url, allowed_domains)
    ):
        return None
    official_date = _as_date(published_at)
    event_id = str(event.get("id") or event.get("external_id") or source_url)
    label_id = "event-label-v3:" + hashlib.sha256(
        f"{event_id}|{disease_id}|{official_date.isoformat()}".encode()
    ).hexdigest()[:24]
    confidence = str(event.get("confidence") or "medium").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    metadata = event.get("metadata") or {}
    started_at = metadata.get("event_started_at") or event.get("event_started_at")
    started_date = _as_date(started_at) if started_at else None
    if started_date and started_date > official_date:
        return None
    return {
        "label_id": label_id,
        "disease_id": disease_id,
        "geographies": list(geographies),
        "event_started_at": started_date,
        "first_official_published_at": official_date,
        "authoritative_source": str(event.get("source") or urlparse(source_url).hostname),
        "source_url": source_url,
        "confidence": confidence,
        "adjudication": "positive",
        "split": "unassigned",
        "evidence": {
            "official_event_id": event_id,
            "title": event.get("title"),
            "evidence_links": event.get("evidence_links") or [],
        },
    }


def summarize_locked_event_replay(
    labels: Iterable[Any],
    *,
    challenger_matches: Iterable[EventLabelMatch],
    champion_matches: Iterable[EventLabelMatch],
) -> dict[str, Any]:
    """Summarize event-level performance without treating unknowns as negatives."""

    eligible_ids = {
        str(_field(label, "label_id"))
        for label in labels
        if _field(label, "split", "unassigned") == "locked_test"
        and _field(label, "adjudication", "indeterminate") == "positive"
    }
    challenger_by_id: dict[str, EventLabelMatch] = {}
    for match in challenger_matches:
        if match.label_id not in eligible_ids:
            continue
        current = challenger_by_id.get(match.label_id)
        if current is None or match.period_distance < current.period_distance:
            challenger_by_id[match.label_id] = match
    champion_ids = {
        match.label_id for match in champion_matches if match.label_id in eligible_ids
    }
    trials = len(eligible_ids)
    detected = len(challenger_by_id)
    champion_detected = len(champion_ids)
    distances = [match.period_distance for match in challenger_by_id.values()]
    leading = sum(distance <= -1 for distance in distances)
    return {
        "locked_positive_event_trials": trials,
        "detected_events": detected,
        "event_detection_rate": round(detected / trials, 6) if trials else None,
        "champion_detected_events": champion_detected,
        "champion_event_detection_rate": (
            round(champion_detected / trials, 6) if trials else None
        ),
        "median_signal_to_official_periods": (
            float(statistics.median(distances)) if distances else None
        ),
        "events_leading_at_least_one_period": leading,
        "leading_at_least_one_period_rate": (
            round(leading / trials, 6) if trials else None
        ),
        "indeterminate_and_unassigned_excluded": True,
    }


__all__ = [
    "EventLabelMatch",
    "VALID_SPLITS",
    "assign_temporal_splits",
    "authoritative_source_url",
    "label_from_official_event",
    "match_signal_to_labels",
    "period_distance",
    "summarize_locked_event_replay",
]
