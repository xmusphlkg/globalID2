"""Build and commit public disease-series rows within a verified site release.

The row builder is pure. Database refresh reuses the site export projection,
then replaces that country's published rows in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.database import get_db
from src.core.logging import get_logger
from src.domain.public_series import CountryPublishState, PublicDiseaseSeries
from src.generation.site_data_catalogue import (
    enrich_diseases_with_ontology,
    load_standard_diseases,
)
from src.generation.site_data_export import ROOT, _collect_country_export
from src.generation.site_data_queries import (
    fetch_countries,
    fetch_country_briefs,
    has_population_table,
    has_table,
)
from src.knowledge.catalogue import should_generate_public_disease_page
from src.ontology import load_disease_ontology

logger = get_logger(__name__)

PROJECTION_VERSION = "public-series-v1"
_EMPTY_COUNTRY_HASH = hashlib.sha256(f"{PROJECTION_VERSION}\n".encode()).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored_count(value: Any) -> int | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number)


def _stored_rate(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _period_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _source_series_code(series: dict) -> str | None:
    selected = [
        str(code)
        for code in (series.get("selected_series_codes") or [])
        if str(code or "").strip()
    ]
    if len(selected) == 1:
        return selected[0]
    points = [
        str(code)
        for code in (series.get("point_series_codes") or [])
        if str(code or "").strip()
    ]
    if points:
        return Counter(points).most_common(1)[0][0]
    return selected[0] if selected else None


def series_content_hash(payload: dict) -> str:
    """Hash the stored curve, not display names or generation timestamps."""

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def country_publish_hash(series_hashes: Iterable[str]) -> str:
    lines = [PROJECTION_VERSION, *sorted(series_hashes)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def country_hash_from_disease_series(country_code: str, disease_series: dict) -> str:
    """Hash an exported country curve the same way the publish table does."""

    hashes: list[str] = []
    for disease_id, series in sorted((disease_series or {}).items()):
        if not isinstance(series, dict):
            continue
        row = build_public_series_row(
            country_code,
            str(disease_id),
            series,
            source_high_water=None,
        )
        if row is not None:
            hashes.append(row["content_hash"])
    return country_publish_hash(hashes) if hashes else _EMPTY_COUNTRY_HASH


def build_public_series_row(
    country_code: str,
    disease_id: str,
    series: dict,
    *,
    source_high_water: datetime | None,
) -> dict | None:
    """Turn one projected disease curve into a publish-table row.

    Diseases with no periods are omitted. Array lengths must match because the
    table stores parallel columns rather than one row per period.
    """

    raw_dates = list(series.get("dates") or [])
    if not raw_dates:
        return None
    period_starts = [_period_date(value) for value in raw_dates]
    cases = [_stored_count(value) for value in (series.get("cases") or [])]
    deaths = [_stored_count(value) for value in (series.get("deaths") or [])]
    incidence = [_stored_rate(value) for value in (series.get("incidence_rates") or [])]
    width = len(period_starts)
    if len(cases) != width or len(deaths) != width or len(incidence) != width:
        raise ValueError(
            f"{country_code} {disease_id} projected arrays differ in length: "
            f"dates={width}, cases={len(cases)}, deaths={len(deaths)}, "
            f"incidence={len(incidence)}"
        )
    payload = {
        "projection_version": PROJECTION_VERSION,
        "country_code": country_code,
        "disease_id": disease_id,
        "granularity": str(series.get("period_granularity") or "unknown")[:40],
        "data_layer": str(series.get("data_layer") or "legacy")[:40],
        "source_series_code": _source_series_code(series),
        "period_starts": [value.isoformat() for value in period_starts],
        "cases": cases,
        "deaths": deaths,
        "incidence": incidence,
    }
    observed_cases = [value for value in cases if value is not None]
    observed_deaths = [value for value in deaths if value is not None]
    return {
        "country_code": country_code,
        "disease_id": disease_id,
        "period_starts": period_starts,
        "cases": cases,
        "deaths": deaths,
        "incidence": incidence,
        "granularity": payload["granularity"],
        "data_layer": payload["data_layer"],
        "source_series_code": payload["source_series_code"],
        "point_count": width,
        "total_cases": int(sum(observed_cases)),
        "total_deaths": int(sum(observed_deaths)),
        "period_start": period_starts[0],
        "period_end": period_starts[-1],
        "content_hash": series_content_hash(payload),
        "source_high_water": _as_utc(source_high_water),
        "projection_version": PROJECTION_VERSION,
    }


def _catalogue_context() -> dict:
    csv_path = Path(ROOT) / "configs" / "standard_diseases.csv"
    catalogue = load_standard_diseases(csv_path)
    catalogue_ids = {disease["disease_id"] for disease in catalogue}
    diseases = [
        disease for disease in catalogue if should_generate_public_disease_page(disease)
    ]
    enrich_diseases_with_ontology(diseases, load_disease_ontology())
    return {
        "catalogue_ids": catalogue_ids,
        "diseases_by_id": {disease["disease_id"]: disease for disease in diseases},
    }


async def _load_publish_context() -> dict:
    context = _catalogue_context()
    async with get_db() as session:
        context["population_enabled"] = await has_population_table(session)
        context["registry_tables_exist"] = await has_table(
            session, "disease_surveillance_series"
        ) and await has_table(session, "disease_series_observations")
        context["country_briefs"] = await fetch_country_briefs(session)
        countries = await fetch_countries(session)
        context["countries_by_code"] = {
            country["code"]: country for country in countries
        }
        context["country_name_by_code"] = {
            country["code"]: country.get("name_en")
            or country.get("name")
            or country["code"]
            for country in countries
        }
        context["country_name_zh_by_code"] = {
            country["code"]: country.get("name_local") or country.get("name")
            for country in countries
        }
        present = await session.execute(
            text("SELECT disease_id FROM standard_diseases")
        )
        context["stored_disease_ids"] = {row[0] for row in present}
    return context


async def _source_high_water(session, country_code: str) -> datetime | None:
    row = await session.execute(
        text(
            """
            SELECT max(updated_at)
            FROM disease_series_observations
            WHERE geography_key = :geography_key
            """
        ),
        {"geography_key": f"country:{country_code}:national"},
    )
    return _as_utc(row.scalar())


async def _replace_country_rows(
    session,
    country_code: str,
    rows: list[dict],
    *,
    source_high_water: datetime | None,
    stored_disease_ids: set[str],
) -> None:
    missing = sorted(
        {
            row["disease_id"]
            for row in rows
            if row["disease_id"] not in stored_disease_ids
        }
    )
    if missing:
        raise RuntimeError(
            "Projected diseases are missing from standard_diseases: "
            + ", ".join(missing)
        )
    now = datetime.now(timezone.utc)
    await session.execute(
        delete(PublicDiseaseSeries).where(
            PublicDiseaseSeries.country_code == country_code
        )
    )
    if rows:
        for row in rows:
            row["created_at"] = now
            row["updated_at"] = now
        await session.execute(pg_insert(PublicDiseaseSeries), rows)

    country_hash = (
        country_publish_hash(row["content_hash"] for row in rows)
        if rows
        else _EMPTY_COUNTRY_HASH
    )
    state = {
        "country_code": country_code,
        "source_high_water": _as_utc(source_high_water),
        "series_count": len(rows),
        "point_count": sum(row["point_count"] for row in rows),
        "content_hash": country_hash,
        "projection_version": PROJECTION_VERSION,
        "published_at": now,
        "created_at": now,
        "updated_at": now,
    }
    insert_state = pg_insert(CountryPublishState).values(state)
    await session.execute(
        insert_state.on_conflict_do_update(
            index_elements=["country_code"],
            set_={
                "source_high_water": insert_state.excluded.source_high_water,
                "series_count": insert_state.excluded.series_count,
                "point_count": insert_state.excluded.point_count,
                "content_hash": insert_state.excluded.content_hash,
                "projection_version": insert_state.excluded.projection_version,
                "published_at": insert_state.excluded.published_at,
                "updated_at": insert_state.excluded.updated_at,
            },
        )
    )


async def _publish_country(
    session, country_code: str, context: dict, *, force: bool
) -> dict:
    country = context["countries_by_code"].get(country_code)
    if country is None:
        return {
            "country_code": country_code,
            "status": "skipped",
            "reason": "not_public",
        }
    if not force:
        current = await session.scalar(
            select(CountryPublishState.projection_version).where(
                CountryPublishState.country_code == country_code
            )
        )
        if current == PROJECTION_VERSION:
            return {
                "country_code": country_code,
                "status": "skipped",
                "reason": "current",
            }

    started = time.perf_counter()
    collected = await _collect_country_export(
        country,
        country_name_by_code=context["country_name_by_code"],
        country_name_zh_by_code=context["country_name_zh_by_code"],
        diseases_by_id=context["diseases_by_id"],
        catalogue_ids=context["catalogue_ids"],
        country_briefs=context["country_briefs"],
        population_enabled=context["population_enabled"],
        registry_tables_exist=context["registry_tables_exist"],
        session=session,
    )
    high_water = await _source_high_water(session, country_code)
    rows = []
    for disease_id, series in sorted(
        (collected["country_data"].get("disease_series") or {}).items()
    ):
        row = build_public_series_row(
            country_code,
            disease_id,
            series,
            source_high_water=high_water,
        )
        if row is not None:
            rows.append(row)
    await _replace_country_rows(
        session,
        country_code,
        rows,
        source_high_water=high_water,
        stored_disease_ids=context["stored_disease_ids"],
    )
    # The raw fetch is only an input to the projection. Drop it before commit
    # so a worker does not keep both representations.
    collected.clear()
    elapsed = time.perf_counter() - started
    logger.info(
        f"Public series {country_code} series={len(rows)} "
        f"points={sum(row['point_count'] for row in rows)} in {elapsed:.1f}s"
    )
    return {
        "country_code": country_code,
        "status": "refreshed",
        "series_count": len(rows),
        "point_count": sum(row["point_count"] for row in rows),
        "elapsed_seconds": round(elapsed, 3),
    }
