"""Run the registered international authority adapters and merge their records.

One call here answers "what have the official cross-border authorities published
recently?" for every source in
:data:`~src.services.global_sources.catalogue.SOURCE_REGISTRY`.  Sources are
isolated from each other: a failing feed downgrades to ``failed`` in the run
status while the remaining adapters still contribute, and a source whose
redistribution terms are not cleared is reported as ``skipped`` instead of being
queried at all.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from .catalogue import SOURCE_REGISTRY
from . import africa_cdc, ecdc_cdtr, paho, who_don, who_periodicals
from .events import utc_now

#: How far back a scheduled run looks by default.  DON publishes a handful of
#: notices a month and CDTR is weekly, so a 120-day window keeps roughly one
#: quarter of official cross-border activity available to the site.
DEFAULT_SINCE_DAYS = 120


def _load_who_don(*, since: date, limit: int) -> list[dict[str, Any]]:
    return who_don.fetch_items(limit=limit, since=since.isoformat())


def _load_ecdc_cdtr(**_: Any) -> list[dict[str, Any]]:
    # The CDTR feed only exposes the ten most recent issues, so no window is
    # passed down; the collection step trims anything outside the range.
    return ecdc_cdtr.fetch_items()


def _load_who_wer(*, since: date, limit: int) -> list[dict[str, Any]]:
    return who_periodicals.fetch_wer(limit=max(1, min(int(limit), 200)), since=since.isoformat())


def _load_who_wpro(**_: Any) -> list[dict[str, Any]]:
    return who_periodicals.fetch_wpro()


def _load_africa_cdc(*, since: date, **_: Any) -> list[dict[str, Any]]:
    return africa_cdc.fetch_items(since=since.isoformat())


def _load_paho(*, since: date, **_: Any) -> list[dict[str, Any]]:
    return paho.fetch_items(since=since.isoformat())


SOURCE_LOADERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "who_don": _load_who_don,
    "ecdc_cdtr": _load_ecdc_cdtr,
    "who_wer": _load_who_wer,
    "who_wpro": _load_who_wpro,
    "africa_cdc": _load_africa_cdc,
    "paho": _load_paho,
}


def collect(
    *,
    since_days: int = DEFAULT_SINCE_DAYS,
    don_limit: int = 200,
    sources: list[str] | tuple[str, ...] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Collect every dated authority record published inside the window.

    Returns a run envelope with ``events`` (newest first) and one ``sources``
    entry per adapter so a caller can show which feeds actually responded.
    """

    today = today or utc_now().date()
    window = max(0, int(since_days))
    since = today - timedelta(days=window)
    selected = [source_id for source_id in (tuple(sources) if sources is not None else tuple(SOURCE_REGISTRY))]
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    statuses: list[dict[str, Any]] = []

    for source_id in selected:
        registry = SOURCE_REGISTRY.get(source_id, {})
        status: dict[str, Any] = {
            "source_id": source_id,
            "label_en": (registry.get("label") or {}).get("en"),
            "label_zh": (registry.get("label") or {}).get("zh"),
            "endpoint": registry.get("endpoint"),
            "homepage": registry.get("homepage"),
            "redistribution": registry.get("redistribution"),
            "event_count": 0,
            "undated_records": 0,
            "status": "ok",
            "error": None,
        }
        if source_id not in SOURCE_LOADERS:
            status["status"] = "skipped"
            status["error"] = "no adapter registered"
            statuses.append(status)
            continue
        if registry.get("redistribution") == "blocked":
            status["status"] = "skipped"
            status["error"] = "redistribution terms not cleared"
            statuses.append(status)
            continue
        try:
            raw = SOURCE_LOADERS[source_id](since=since, limit=don_limit)
        except Exception as exc:  # noqa: BLE001 - one failing feed must not abort the run
            status["status"] = "failed"
            status["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            statuses.append(status)
            continue

        for event in raw:
            published = event.get("published_date")
            if not published:
                status["undated_records"] += 1
                continue
            # Records without a date cannot join a week-indexed timeline, and
            # anything older than the window is dropped even when the source
            # ignores the ``since`` parameter.
            try:
                if date.fromisoformat(str(published)) < since:
                    continue
            except ValueError:
                status["undated_records"] += 1
                continue
            fingerprint = str(event.get("fingerprint") or event.get("event_id"))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            events.append(event)
            status["event_count"] += 1
        statuses.append(status)

    events.sort(
        key=lambda row: (row.get("published_date") or "", row.get("event_id") or ""),
        reverse=True,
    )
    return {
        "generated_at": utc_now().isoformat(),
        "since_days": window,
        "since_date": since.isoformat(),
        "as_of": today.isoformat(),
        "sources": statuses,
        "events": events,
    }
