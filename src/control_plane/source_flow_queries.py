"""Source-flow queries; the caller owns rollup caching and its lock."""
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.domain.country import Country
from src.domain.disease_ontology import DiseaseSurveillanceSeries, DiseaseSourceAvailability


def _flow_bucket() -> dict:
    return {
        "series_ids": set(),
        "observation_count": 0,
        "earliest": None,
        "latest": None,
    }

def _flow_dates(bucket: dict, earliest, latest) -> None:
    if earliest is not None and (bucket["earliest"] is None or earliest < bucket["earliest"]):
        bucket["earliest"] = earliest
    if latest is not None and (bucket["latest"] is None or latest > bucket["latest"]):
        bucket["latest"] = latest

async def load_series_flow_facts(db: AsyncSession, country_id: int | None, *, observation_rollup, geography_sources):
    """Build the series, definition, quality, and availability rows for source flow.

    Geography-owned sources are attributed to the country encoded on each
    national observation. Every other source keeps the series' own country,
    including series that do not have observations yet.
    """

    rollup = await observation_rollup(db)
    countries = (
        await db.execute(select(Country.id, Country.code, Country.name_en))
    ).all()
    country_by_code = {row.code: row for row in countries}
    series_list = (
        await db.execute(
            select(
                DiseaseSurveillanceSeries.series_code,
                DiseaseSurveillanceSeries.source_system,
                DiseaseSurveillanceSeries.country_code,
                DiseaseSurveillanceSeries.availability_status,
                DiseaseSurveillanceSeries.metric_type,
                DiseaseSurveillanceSeries.mapping_relation,
                DiseaseSurveillanceSeries.comparability,
            )
        )
    ).all()
    series_by_code = {row.series_code: row for row in series_list}

    series_buckets: dict[tuple, dict] = {}
    definition_counts: dict[tuple, set] = {}
    quality_counts: dict[tuple, int] = {}

    def country_ok(country) -> bool:
        return country is not None and (country_id is None or country.id == country_id)

    def series_bucket(country, source_system: str) -> dict:
        key = (country.id, source_system)
        bucket = series_buckets.get(key)
        if bucket is None:
            bucket = _flow_bucket()
            bucket.update(
                {
                    "country_id": country.id,
                    "country_code": country.code,
                    "country_name": country.name_en,
                    "source_system": source_system,
                }
            )
            series_buckets[key] = bucket
        return bucket

    for series in series_list:
        if series.source_system in geography_sources:
            continue
        country = country_by_code.get(series.country_code)
        if not country_ok(country):
            continue
        bucket = series_bucket(country, series.source_system)
        bucket["series_ids"].add(series.series_code)
        definition_key = (
            country.id,
            country.code,
            series.source_system,
            series.availability_status,
            series.metric_type,
            series.mapping_relation,
            series.comparability,
        )
        definition_counts.setdefault(definition_key, set()).add(series.series_code)

    national_countries_by_series: dict[str, set[str]] = {}
    for series_code, national_country, quality_status, count, earliest, latest in rollup:
        series = series_by_code.get(series_code)
        if series is None or count <= 0:
            continue
        geography_owned = series.source_system in geography_sources
        if geography_owned:
            if not national_country:
                continue
            country = country_by_code.get(national_country)
            if not country_ok(country):
                continue
            national_countries_by_series.setdefault(series_code, set()).add(national_country)
            definition_key = (
                country.id,
                country.code,
                series.source_system,
                series.availability_status,
                series.metric_type,
                series.mapping_relation,
                series.comparability,
            )
            definition_counts.setdefault(definition_key, set()).add(series_code)
        else:
            country = country_by_code.get(series.country_code)
            if not country_ok(country):
                continue
        bucket = series_bucket(country, series.source_system)
        bucket["series_ids"].add(series_code)
        bucket["observation_count"] += count
        _flow_dates(bucket, earliest, latest)
        quality_key = (country.id, country.code, series.source_system, quality_status)
        quality_counts[quality_key] = quality_counts.get(quality_key, 0) + count

    series_rows = [
        SimpleNamespace(
            country_id=bucket["country_id"],
            country_code=bucket["country_code"],
            country_name=bucket["country_name"],
            source_system=bucket["source_system"],
            series_count=len(bucket["series_ids"]),
            observation_count=bucket["observation_count"],
            earliest_date=bucket["earliest"],
            latest_date=bucket["latest"],
        )
        for bucket in series_buckets.values()
    ]
    definition_rows = [
        SimpleNamespace(
            country_id=key[0],
            country_code=key[1],
            source_system=key[2],
            availability_status=key[3],
            metric_type=key[4],
            mapping_relation=key[5],
            comparability=key[6],
            count=len(series_ids),
        )
        for key, series_ids in definition_counts.items()
    ]
    quality_rows = [
        SimpleNamespace(
            country_id=key[0],
            country_code=key[1],
            source_system=key[2],
            quality_status=key[3],
            count=count,
        )
        for key, count in quality_counts.items()
    ]

    availability_rows = (
        await db.execute(
            select(
                Country.id.label("country_id"),
                Country.code.label("country_code"),
                DiseaseSourceAvailability.source_system,
                DiseaseSourceAvailability.status,
                func.count().label("count"),
            )
            .select_from(DiseaseSourceAvailability)
            .join(Country, Country.code == DiseaseSourceAvailability.country_code)
            .where(
                DiseaseSourceAvailability.source_system.not_in(
                    geography_sources
                )
            )
            .group_by(
                Country.id,
                Country.code,
                DiseaseSourceAvailability.source_system,
                DiseaseSourceAvailability.status,
            )
        )
    ).all()
    if country_id is not None:
        availability_rows = [row for row in availability_rows if row.country_id == country_id]

    geo_availability = (
        await db.execute(
            select(
                DiseaseSourceAvailability.source_system,
                DiseaseSourceAvailability.status,
                DiseaseSourceAvailability.availability_code,
                DiseaseSourceAvailability.series_code,
            ).where(
                DiseaseSourceAvailability.source_system.in_(
                    geography_sources
                ),
                DiseaseSourceAvailability.series_code.is_not(None),
            )
        )
    ).all()
    geo_counts: dict[tuple, set] = {}
    for row in geo_availability:
        for code in national_countries_by_series.get(row.series_code, ()):
            country = country_by_code.get(code)
            if not country_ok(country):
                continue
            geo_counts.setdefault((country.id, country.code, row.source_system, row.status), set()).add(
                row.availability_code
            )
    availability_rows = list(availability_rows) + [
        SimpleNamespace(
            country_id=key[0],
            country_code=key[1],
            source_system=key[2],
            status=key[3],
            count=len(codes),
        )
        for key, codes in geo_counts.items()
    ]
    return series_rows, definition_rows, quality_rows, availability_rows
