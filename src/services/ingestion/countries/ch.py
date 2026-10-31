"""FOPH IDD source metadata discovery and offline national/canton parsing."""
from __future__ import annotations

import asyncio
import csv
import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.ch import (
    SwitzerlandIDDCrawler, CHSeriesSource, CH_CANTONS,
    choose_canton_series_configs, _display_name_for_slug, _norm_text,
)
from src.data.processors.ch import CHUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.snapshot_merge import merge_snapshot_rows
from src.services.ingestion.capture import capture_writer


async def collect(writer, *, source, country_code, history_csv, force=False, months=None, start_year=None):
    crawler = SwitzerlandIDDCrawler(save_raw=False)
    try:
        cache = Path(history_csv)
        if cache.exists():
            await writer.write(await asyncio.to_thread(cache.read_bytes), source_url=cache.as_posix(), kind="history_csv")
        sources = []
        with capture_writer(writer):
            try:
                if country_code == "CH":
                    sources = await asyncio.to_thread(crawler.fetch_series_sources)
                else:
                    code = country_code.removeprefix("CH-")
                    identifiers = await asyncio.to_thread(crawler.fetch_sets)
                    for identifier in sorted(i for i in identifiers if i.endswith("/cases/value/year")):
                        response = await asyncio.to_thread(crawler.get, crawler._api_url(f"data/{identifier}/details"))
                        details = response.json()
                        config = choose_canton_series_configs(details.get("availableSeriesConfigurations", [])).get(code)
                        if config:
                            slug = identifier.split("/", 1)[0]
                            sources.append(CHSeriesSource(slug, _display_name_for_slug(slug), identifier, "year", config, code, _norm_text(details.get("sourceDate"))))
            except Exception:
                if not any(a.kind == "history_csv" for a in writer.artifacts):
                    raise
                sources = []
        for series in sources:
            try:
                url = crawler._api_url(f"data/{series.identifier}")
                response = await asyncio.to_thread(crawler.post, url, json=series.config)
            except Exception:
                # This source historically tolerates missing individual series.
                continue
            await writer.write(response.content, source_url=url, kind="idd_series", metadata={"series": asdict(series)})
        if not any(a.kind in {"idd_series", "history_csv"} for a in writer.artifacts):
            raise RuntimeError("CH IDD source returned no usable series")
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, country_code, history_csv, force=False, months=None, start_year=None):
    crawler = SwitzerlandIDDCrawler(save_raw=False)
    rows, prior = [], []
    version = None
    logs = []
    live_error = None
    try:
        with tempfile.TemporaryDirectory(prefix="globalid-idd-") as folder:
            root = Path(folder)
            for artifact in artifacts:
                if artifact.kind == "history_csv":
                    path = root / "history.csv"
                    path.write_bytes(read_artifact(artifact))
                    try:
                        prior = updater._load_rows(path)
                    except Exception as exc:
                        logs.append(f"[cache] unable to read existing CSV: {type(exc).__name__}: {exc}")
                elif artifact.kind == "idd_series":
                    series = CHSeriesSource(**artifact.metadata_["series"])
                    payload = read_artifact(artifact)
                    try:
                        parsed, series_version = crawler.parse_series_payload(series, json.loads(payload), url=artifact.source_url)
                    except Exception as exc:
                        live_error = exc
                        logs.append(f"[crawler] series parsing failed: {type(exc).__name__}: {exc}")
                        continue
                    if series_version:
                        version = series_version
                    rows.extend(parsed)
            try:
                if updater.is_subdivision:
                    code = country_code.removeprefix("CH-")
                    years = {year for year, _ in months or []}
                    if not years and not force:
                        current = datetime.now(timezone.utc).year
                        years = {current - 1, current}
                    rows = [r for r in rows if (force or not years or int(r["Year"]) in years) and (start_year is None or int(r["Year"]) >= start_year)]
                    for row in rows:
                        row.update(Geography=CH_CANTONS[code], GeographyKey=f"country:{country_code}:national", Geocode=country_code, ReportingArea=CH_CANTONS[code], ParentCountryCode="CH", LocationType="subdivision")
                else:
                    rows = crawler._filter_rows_for_months(rows, months) if months is not None else rows if force else crawler._filter_recent_rows(rows)
                    rows = crawler._filter_rows_from_year(rows, start_year)
                    # National output previously omits these source-derived keys.
                    fields = {"Date", "RawDiseaseLabel", "DiseaseCode", "Year", "Month", "ISOWeek", "PeriodType", "PeriodValue", "Cases", "Geography", "Group", "DataComplete", "Trend", "SourceDate", "Version", "Source", "SourceURL"}
                    rows = [{k: v for k, v in row.items() if k in fields} for row in rows]
                if rows:
                    path = root / "normalized.csv"
                    fields = sorted({k for r in rows for k in r})
                    with path.open("w", newline="") as stream:
                        writer = csv.DictWriter(stream, fieldnames=fields)
                        writer.writeheader()
                        writer.writerows(rows)
                    rows = updater._load_rows(path)
            except Exception as exc:
                rows = []
                live_error = exc
                logs.append(f"[crawler] live normalization failed: {type(exc).__name__}: {exc}")
            if months is not None:
                if updater.is_subdivision:
                    years = {year for year, _ in months}
                    rows = updater._filter_rows_for_years(rows, years)
                    prior = updater._filter_rows_for_years(prior, years)
                else:
                    rows = updater._filter_rows_for_months(rows, months)
                    prior = updater._filter_rows_for_months(prior, months)
            selected, source_audit = merge_snapshot_rows(
                rows, prior, dimensions=("PeriodType", "PeriodValue", "GeographyKey", "Group"),
            )
            if not selected:
                if live_error is not None:
                    raise live_error
                raise RuntimeError("CH IDD crawler produced no usable rows")
            if selected is prior:
                logs.append(f"[recovery] using previous CSV snapshot with {len(selected)} rows")
            return CHUpdateFetchResult(selected, updater._latest_row_date(selected), Path(history_csv), logs, version, [source_audit])
    finally:
        crawler.session.close()
