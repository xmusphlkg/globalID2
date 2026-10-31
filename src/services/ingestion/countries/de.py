"""RKI session/export transport followed by offline source-row normalization."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import date
from pathlib import Path

from src.data.crawlers.de import (
    GERMAN_STATES,
    NATIONAL_GEOGRAPHY_KEY,
    DESurvStatContractError,
    GermanySurvStatCrawler,
    parse_survstat_zip,
)
from src.data.processors.de import DEUpdateFetchResult, _parse_cases, _parse_date, _text
from src.services.ingestion.artifacts import read_artifact


def validate_source(source):
    if str(source).strip().lower() not in {
        "all",
        "de",
        "germany",
        "rki",
        "survstat",
        "rki_survstat",
    }:
        raise ValueError(f"Unsupported DE source: {source!r}")


async def collect(
    writer,
    *,
    source,
    country_code="DE",
    weeks,
    export_url_template="",
    history_csv=None,
    **options,
):
    validate_source(source)
    crawler = GermanySurvStatCrawler(export_url_template=export_url_template)
    region = country_code if country_code != "DE" else None
    try:
        for year in sorted({day.isocalendar().year for day in weeks}):
            for attempt in range(3):
                try:
                    raw, url = await asyncio.to_thread(
                        crawler._download_year, year, region
                    )
                    break
                except DESurvStatContractError:
                    if attempt == 2:
                        raise
            await writer.write(
                raw,
                source_url=url,
                kind="rki_zip",
                metadata={"year": year, "region": region},
            )
        if history_csv and Path(history_csv).exists():
            await writer.write(
                await asyncio.to_thread(Path(history_csv).read_bytes),
                source_url=Path(history_csv).as_posix(),
                kind="history_csv",
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, weeks, force=False, **options):
    validate_source(source)
    merged = {}
    fresh = []
    for artifact in artifacts:
        raw = read_artifact(artifact)
        if artifact.kind == "history_csv":
            for cached in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
                key = (
                    _text(cached.get("Date")),
                    _text(cached.get("SourceDiseaseCode")),
                    _text(cached.get("GeographyKey")) or NATIONAL_GEOGRAPHY_KEY,
                )
                if all(key):
                    merged[key] = {key: _text(value) for key, value in cached.items()}
        elif artifact.kind == "rki_zip":
            region = artifact.metadata_["region"]
            fresh.extend(
                parse_survstat_zip(
                    raw,
                    source_url=artifact.source_url,
                    retrieved_at=artifact.created_at,
                    export_year=artifact.metadata_["year"],
                    geography=GERMAN_STATES[region] if region else "Deutschland",
                    geography_key=f"country:{region}:national"
                    if region
                    else NATIONAL_GEOGRAPHY_KEY,
                )
            )
    for row in fresh:
        merged[(row["Date"], row["SourceDiseaseCode"], row["GeographyKey"])] = row
    rows = sorted(
        merged.values(),
        key=lambda row: (
            row["Date"],
            row["SourceDiseaseCode"],
            row.get("GeographyKey", ""),
        ),
    )
    latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
    fields = {key for row in rows for key in row}
    rows = [
        {key: _text(row.get(key, "")) for key in fields}
        for row in rows
        if _parse_date(row) and _parse_cases(row) is not None
    ]
    if not force:
        wanted = set(weeks)
        rows = [row for row in rows if _parse_date(row) in wanted]
    return DEUpdateFetchResult(
        rows,
        latest,
        updater.output_csv,
        [
            f"[artifacts] {len(artifacts)} captured RKI exports; {len(rows)} source observations"
        ],
    )
