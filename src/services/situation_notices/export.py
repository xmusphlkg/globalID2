"""Export the alert database into the static Situation site data.

Layout under ``DEFAULT_OUTPUT_DIR`` (everything is rebuilt on each export):

* ``map.json``: one point per outbreak x place (its latest alert) plus filter facets;
* ``outbreaks/<id>.json``: the alert timeline of one outbreak;
* ``by-country/<CC>.json`` and ``by-disease/<ID>.json``: alerts for entity pages;
* ``monitoring.json``: periodic roundups that raised no map point (hidden by default);
* ``weekly/<period>.json`` and ``weekly/index.json``: what changed that week, as numbers;
* ``sources.json``: source registry joined with the latest run status.

Only structured facts, one quoted sentence and the link are public. Full report
text never leaves the database. A quote is published only for sources whose
redistribution terms are cleared (``metadata_and_extract``).
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.ai.peer_review import review_mode
from src.core.database import get_db
from src.domain import SituationAlert, SituationExtraction, SituationNotice, SituationOutbreak, SituationSourceRun
from src.services.global_sources.catalogue import (
    ROOT,
    SOURCE_REGISTRY,
    country_row,
    disease_catalogue,
    iso_week,
)

from .pipeline import utc_now

SCHEMA_VERSION = "gids.situation_alerts.v1"
DEFAULT_OUTPUT_DIR = ROOT / "astro-site" / "src" / "data" / "situation" / "international"
DEFAULT_SINCE_DAYS = 365
QUOTE_TERMS = "metadata_and_extract"
_OWNED = ("outbreaks", "by-country", "by-disease", "weekly")


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source(source_id: str) -> dict[str, Any]:
    registry = SOURCE_REGISTRY.get(source_id, {})
    return {
        "source_id": source_id,
        "source_label_en": (registry.get("label") or {}).get("en"),
        "source_label_zh": (registry.get("label") or {}).get("zh"),
        "agency_en": (registry.get("agency") or {}).get("en"),
        "agency_zh": (registry.get("agency") or {}).get("zh"),
    }


def _sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (record["published_date"], record["alert_id"])


def alert_record(
    alert: SituationAlert,
    notice: SituationNotice,
    extraction: SituationExtraction | None,
    diseases: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """One alert in the public shape; the quote is withheld where terms are unclear."""

    disease = diseases.get(alert.disease_id or "", {})
    country = country_row(alert.country_code)
    quotable = notice.redistribution == QUOTE_TERMS
    return {
        "alert_id": alert.alert_id,
        "outbreak_id": alert.outbreak_id,
        "notice_id": notice.notice_id,
        **_source(alert.source_id),
        "title_en": notice.title_en,
        "url": notice.url,
        "disease_id": alert.disease_id,
        "disease_slug": disease.get("slug") or None,
        "disease_label": alert.disease_label,
        "disease_name_en": disease.get("name_en") or alert.disease_label,
        "disease_name_zh": disease.get("name_zh"),
        "country_code": alert.country_code,
        "country_name_en": country["name_en"],
        "country_name_zh": country["name_zh"],
        "admin_area": alert.admin_area,
        "status": alert.status,
        "cases": alert.cases,
        "deaths": alert.deaths,
        "count_basis": alert.count_basis,
        "as_of": alert.as_of.isoformat() if alert.as_of else None,
        "published_date": alert.published_date.isoformat(),
        "quote": alert.quote if quotable else None,
        "summary_en": alert.summary_en,
        "summary_zh": alert.summary_zh,
        "summary_fr": alert.summary_fr,
        "ai": (
            {
                "review_mode": review_mode(extraction.author_model, extraction.reviewer_model),
                "input_mode": extraction.input_mode,
                "verdict": extraction.review_verdict,
                "prompt_version": extraction.prompt_version,
            }
            if extraction
            else None
        ),
    }


def latest_per_place(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest alert of each outbreak x country x admin area: one map point each."""

    newest: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for record in sorted(records, key=_sort_key):
        key = (
            record["outbreak_id"] or record["alert_id"],
            record["country_code"],
            record["admin_area"] or "",
            record["disease_id"] or record["disease_label"],
        )
        newest[key] = record
    return sorted(newest.values(), key=_sort_key, reverse=True)


def facets(points: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Filter options with the number of points behind each."""

    def tally(key: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, Any]] = {}
        for point in points:
            value = point.get(key)
            if not value:
                continue
            entry = counts.setdefault(value, {"id": value, **{name: point.get(name) for name in fields}, "count": 0})
            entry["count"] += 1
        return sorted(counts.values(), key=lambda item: (-item["count"], str(item["id"])))

    return {
        "diseases": tally("disease_id", ("disease_name_en", "disease_name_zh", "disease_slug")),
        "countries": tally("country_code", ("country_name_en", "country_name_zh")),
        "sources": tally("source_id", ("source_label_en", "source_label_zh")),
    }


def weekly_changes(
    records: Iterable[dict[str, Any]], outbreaks: Iterable[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Per ISO week: new outbreaks, outbreaks with news, and places seen for the first time."""

    weeks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"new_outbreaks": [], "updated_outbreaks": [], "new_places": 0, "alerts": 0}
    )
    first_seen: dict[tuple[str, str], str] = {}
    for record in sorted(records, key=_sort_key):
        week = iso_week(date.fromisoformat(record["published_date"]))["iso_week_key"]
        weeks[week]["alerts"] += 1
        place = (record["outbreak_id"] or record["alert_id"], record["country_code"])
        if place not in first_seen:
            first_seen[place] = week
            weeks[week]["new_places"] += 1
    for outbreak in outbreaks:
        started = iso_week(date.fromisoformat(outbreak["first_published"]))["iso_week_key"]
        weeks[started]["new_outbreaks"].append(outbreak["outbreak_id"])
        for week in {
            iso_week(date.fromisoformat(item["published_date"]))["iso_week_key"] for item in outbreak["alerts"]
        } - {started}:
            weeks[week]["updated_outbreaks"].append(outbreak["outbreak_id"])
    return {key: weeks[key] for key in sorted(weeks, reverse=True)}


def _outbreak_record(row: SituationOutbreak, alerts: list[dict[str, Any]], diseases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    disease = diseases.get(row.disease_id or "", {})
    ordered = sorted(alerts, key=_sort_key, reverse=True)
    return {
        "outbreak_id": row.outbreak_id,
        "disease_id": row.disease_id,
        "disease_slug": disease.get("slug") or None,
        "disease_label": row.disease_label,
        "disease_name_en": disease.get("name_en") or row.disease_label,
        "disease_name_zh": disease.get("name_zh"),
        "first_published": row.first_published.isoformat(),
        "last_published": row.last_published.isoformat(),
        "country_codes": row.country_codes or [],
        "alert_count": len(ordered),
        "notice_count": row.notice_count,
        "source_ids": row.source_ids or [],
        "status": row.status,
        "alerts": ordered,
    }


def _shard(out: Path, folder: str, grouped: dict[str, list[dict[str, Any]]], head: dict[str, dict[str, Any]], as_of: str) -> None:
    for key, rows in grouped.items():
        rows.sort(key=_sort_key, reverse=True)
        _write(
            out / folder / f"{key}.json",
            {"schema_version": SCHEMA_VERSION, "as_of": as_of, **head[key], "alert_count": len(rows), "alerts": rows},
        )


async def export_site(
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    since_days: int = DEFAULT_SINCE_DAYS,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or utc_now().date()
    since = as_of - timedelta(days=since_days)
    out = Path(output_dir)
    diseases = {row["disease_id"]: row for row in disease_catalogue()}

    async with get_db() as db:
        alert_rows = (
            await db.execute(
                select(SituationAlert)
                .options(selectinload(SituationAlert.notice), selectinload(SituationAlert.extraction))
                .where(SituationAlert.published_date >= since)
            )
        ).scalars().all()
        outbreak_rows = (
            await db.execute(select(SituationOutbreak).where(SituationOutbreak.last_published >= since))
        ).scalars().all()
        monitoring_rows = (
            await db.execute(
                select(SituationNotice, SituationExtraction)
                .join(SituationExtraction, SituationExtraction.notice_id == SituationNotice.notice_id)
                .where(
                    SituationExtraction.status == "published",
                    SituationExtraction.document_type == "monitoring_product",
                    SituationNotice.published_date >= since,
                )
                .order_by(SituationNotice.published_date.desc())
            )
        ).all()
        latest_run = (
            await db.execute(select(SituationSourceRun.run_id).order_by(SituationSourceRun.started_at.desc()).limit(1))
        ).scalar_one_or_none()
        runs = (
            (await db.execute(select(SituationSourceRun).where(SituationSourceRun.run_id == latest_run))).scalars().all()
            if latest_run
            else []
        )

    records = [alert_record(row, row.notice, row.extraction, diseases) for row in alert_rows]
    by_outbreak: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["outbreak_id"]:
            by_outbreak[record["outbreak_id"]].append(record)
    outbreaks = [
        _outbreak_record(row, by_outbreak.get(row.outbreak_id, []), diseases)
        for row in sorted(outbreak_rows, key=lambda item: (item.last_published, item.outbreak_id), reverse=True)
    ]
    outbreaks = [item for item in outbreaks if item["alerts"]]

    # Routine alerts are surveillance background: kept in shards, not on the map.
    points = latest_per_place(record for record in records if record["status"] != "routine")
    generated = utc_now().isoformat()
    stamp = as_of.isoformat()

    for folder in _OWNED:
        shutil.rmtree(out / folder, ignore_errors=True)

    _write(
        out / "map.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated,
            "as_of": stamp,
            "since_date": since.isoformat(),
            "point_count": len(points),
            "points": points,
            "facets": facets(points),
            "outbreaks": [{k: v for k, v in item.items() if k != "alerts"} for item in outbreaks],
        },
    )
    for item in outbreaks:
        _write(out / "outbreaks" / f"{item['outbreak_id']}.json", {"schema_version": SCHEMA_VERSION, "as_of": stamp, **item})

    by_country: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_country[record["country_code"]].append(record)
        if record["disease_id"]:
            by_disease[record["disease_id"]].append(record)
    _shard(out, "by-country", by_country, {code: {"country": country_row(code)} for code in by_country}, stamp)
    _shard(
        out,
        "by-disease",
        by_disease,
        {key: {"disease": {k: diseases.get(key, {}).get(k) for k in ("disease_id", "name_en", "name_zh", "slug")}} for key in by_disease},
        stamp,
    )

    changes = weekly_changes(records, outbreaks)
    for period, change in changes.items():
        _write(out / "weekly" / f"{period}.json", {"schema_version": SCHEMA_VERSION, "period_key": period, **change})
    _write(
        out / "weekly" / "index.json",
        {"schema_version": SCHEMA_VERSION, "periods": [{"period_key": key, **value} for key, value in changes.items()]},
    )

    _write(
        out / "monitoring.json",
        {
            "schema_version": SCHEMA_VERSION,
            "as_of": stamp,
            "products": [
                {
                    "notice_id": notice.notice_id,
                    **_source(notice.source_id),
                    "title_en": notice.title_en,
                    "url": notice.url,
                    "published_date": notice.published_date.isoformat(),
                    "alert_count": extraction.alert_count,
                }
                for notice, extraction in monitoring_rows
            ],
        },
    )
    _write(
        out / "sources.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated,
            "sources": [
                {
                    **registry,
                    **{
                        key: getattr(next((run for run in runs if run.source_id == source_id), None), attr, None)
                        for key, attr in (("status", "status"), ("fetched_count", "fetched_count"), ("error", "error"))
                    },
                }
                for source_id, registry in SOURCE_REGISTRY.items()
            ],
        },
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "output_dir": str(out),
        "alert_count": len(records),
        "point_count": len(points),
        "outbreak_count": len(outbreaks),
        "monitoring_products": len(monitoring_rows),
        "weeks": len(changes),
    }


__all__ = ["DEFAULT_OUTPUT_DIR", "SCHEMA_VERSION", "alert_record", "export_site", "facets", "latest_per_place", "weekly_changes"]
