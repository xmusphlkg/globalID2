"""Write the collected authority records into the Astro site data tree.

The export is deliberately sharded.  A page that needs "what happened recently"
reads one small file; a page about a single disease or country reads that shard;
the per-year archive files carry the full records.  Nothing in the renderer ever
has to load every event at once.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .catalogue import ROOT, SOURCE_REGISTRY

SCHEMA_VERSION = "gids.international_authority_events.v1"
DEFAULT_OUTPUT_DIR = ROOT / "astro-site" / "src" / "data" / "situation" / "international"
DEFAULT_RECENT_LIMIT = 120

_FULL_FIELDS = (
    "event_id",
    "source_id",
    "native_id",
    "record_type",
    "title_en",
    "title_zh",
    "summary_en",
    "summary_zh",
    "url",
    "source_label_en",
    "source_label_zh",
    "agency_en",
    "agency_zh",
    "language",
    "published_date",
    "updated_at_source",
    "iso_year",
    "iso_week",
    "iso_week_key",
    "diseases",
    "countries",
    "retrieved_at",
    "fingerprint",
    # Peer-reviewed AI enrichment; null until a reviewer model accepted it.
    "title_fr",
    "summary_fr",
    "event_type",
    "risk_level",
    "risk_rationale",
    "ai",
)

_REF_FIELDS = (
    "event_id",
    "source_id",
    "record_type",
    "title_en",
    "title_zh",
    "url",
    "source_label_en",
    "source_label_zh",
    "published_date",
    "iso_year",
    "iso_week",
    "iso_week_key",
    "diseases",
    "countries",
    "title_fr",
    "summary_en",
    "summary_zh",
    "summary_fr",
    "event_type",
    "risk_level",
)


def full_record(event: dict[str, Any]) -> dict[str, Any]:
    """Keep only the documented fields, always in the same order."""

    return {key: event.get(key) for key in _FULL_FIELDS}


def reference_record(event: dict[str, Any]) -> dict[str, Any]:
    """Compact projection used by the disease and country shards."""

    return {key: event.get(key) for key in _REF_FIELDS}


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("published_date") or ""), str(row.get("event_id") or ""))


def _entity_counts(events: Iterable[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    tally: dict[str, dict[str, Any]] = {}
    for event in events:
        for row in event.get(field) or []:
            identifier = str(row.get("disease_id") or row.get("code") or "")
            if not identifier:
                continue
            entry = tally.setdefault(
                identifier,
                {
                    "disease_id" if field == "diseases" else "code": identifier,
                    "name_en": row.get("name_en"),
                    "name_zh": row.get("name_zh"),
                    "event_count": 0,
                },
            )
            entry["event_count"] += 1
    rows = list(tally.values())
    rows.sort(key=lambda row: (-row["event_count"], str(row.get("name_en") or "")))
    return rows


def _week_index(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    tally: dict[str, dict[str, Any]] = {}
    for event in events:
        week = event.get("iso_week_key")
        if not week:
            continue
        entry = tally.setdefault(
            str(week),
            {
                "iso_week_key": week,
                "iso_year": event.get("iso_year"),
                "iso_week": event.get("iso_week"),
                "event_count": 0,
            },
        )
        entry["event_count"] += 1
    rows = list(tally.values())
    rows.sort(key=lambda row: str(row["iso_week_key"]), reverse=True)
    return rows


# --- export ---

def export_events(
    run: dict[str, Any],
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    """Write a run envelope into ``output_dir`` and return a run summary."""

    out = Path(output_dir)
    events = [full_record(event) for event in run.get("events") or []]
    sources = list(run.get("sources") or [])
    recent = events[: max(1, int(recent_limit))]

    # Shards are rebuilt from scratch so an entity that drops out of the window
    # never leaves a stale file behind.
    for directory in ("archive", "by-disease", "by-country"):
        target = out / directory
        if target.exists():
            shutil.rmtree(target)

    _write(
        out / "latest.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": run.get("generated_at"),
            "window": {
                "since_days": run.get("since_days"),
                "since_date": run.get("since_date"),
                "as_of": run.get("as_of"),
            },
            "event_count": len(events),
            "recent_event_count": len(recent),
            "recent": recent,
            "iso_weeks": _week_index(events),
            "diseases": _entity_counts(events, "diseases"),
            "countries": _entity_counts(events, "countries"),
            "sources": sources,
        },
    )
    _write(
        out / "sources.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": run.get("generated_at"),
            "sources": [
                {
                    **SOURCE_REGISTRY.get(str(row.get("source_id") or ""), {}),
                    **{key: row.get(key) for key in ("status", "event_count", "undated_records", "error")},
                }
                for row in sources
            ],
        },
    )

    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_country: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        year = event.get("iso_year") or str(event.get("published_date") or "")[:4]
        if year:
            by_year[str(year)].append(event)
        reference = reference_record(event)
        for row in event.get("diseases") or []:
            identifier = str(row.get("disease_id") or "")
            if identifier:
                by_disease[identifier].append(reference)
        for row in event.get("countries") or []:
            code = str(row.get("code") or "").upper()
            if code:
                by_country[code].append(reference)

    for year, rows in by_year.items():
        rows.sort(key=_sort_key, reverse=True)
        _write(
            out / "archive" / f"{year}.json",
            {"schema_version": SCHEMA_VERSION, "iso_year": year, "event_count": len(rows), "events": rows},
        )

    for disease_id, rows in by_disease.items():
        rows.sort(key=_sort_key, reverse=True)
        disease = {"disease_id": disease_id, "name_en": None, "name_zh": None, "slug": None}
        for row in rows:
            for candidate in row.get("diseases") or []:
                if str(candidate.get("disease_id") or "").upper() == str(disease_id).upper():
                    disease = dict(candidate)
                    break
            else:
                continue
            break
        _write(
            out / "by-disease" / f"{disease_id}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "iso_years": sorted({str(row.get("iso_year")) for row in rows if row.get("iso_year")}, reverse=True),
                "disease": disease,
                "event_count": len(rows),
                "events": rows,
            },
        )

    for code, rows in by_country.items():
        rows.sort(key=_sort_key, reverse=True)
        country = {"code": code, "name_en": None, "name_zh": None}
        for row in rows:
            for candidate in row.get("countries") or []:
                if str(candidate.get("code") or "").upper() == code:
                    country = dict(candidate)
                    break
            if country.get("name_en"):
                break
        _write(
            out / "by-country" / f"{code}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "iso_years": sorted({str(row.get("iso_year")) for row in rows if row.get("iso_year")}, reverse=True),
                "country": country,
                "event_count": len(rows),
                "events": rows,
            },
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "output_dir": str(out),
        "event_count": len(events),
        "recent_event_count": len(recent),
        "archive_years": sorted(by_year),
        "disease_shards": len(by_disease),
        "country_shards": len(by_country),
        "sources": [
            {
                "source_id": row.get("source_id"),
                "status": row.get("status"),
                "event_count": row.get("event_count"),
                "undated_records": row.get("undated_records"),
                "error": row.get("error"),
            }
            for row in sources
        ],
    }
