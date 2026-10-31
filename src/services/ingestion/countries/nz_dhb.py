"""PHF annual DHB CSV stays separate from national monthly surveillance."""
import asyncio
from datetime import date
from pathlib import Path

import requests

from src.data.crawlers.nz import NewZealandPHFCrawler, DHB_TOTAL_CSV_MIRRORS
from src.data.processors.nz import NZUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact


def validate_source(source):
    if source.casefold() not in {"all", "phf_dhb_annual", "dhb", "nz_dhb"}:
        raise ValueError(f"Unsupported NZ DHB source: {source}")


async def collect(writer, *, source, country_code, output_csv):
    validate_source(source)
    crawler = NewZealandPHFCrawler()
    try:
        last_error = None
        for url in DHB_TOTAL_CSV_MIRRORS:
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except requests.RequestException as exc:
                last_error = exc
                continue
            await writer.write(response.content, source_url=url, kind="annual_csv")
            return writer.artifacts
        raise RuntimeError("[NZ-DHB] PHF annual CSV mirrors failed") from last_error
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, country_code, output_csv):
    validate_source(source)
    rows = []
    for artifact in artifacts:
        if artifact.kind == "annual_csv":
            parsed, _ = NewZealandPHFCrawler.parse_dhb_annual_csv(read_artifact(artifact), source_url=artifact.source_url)
            rows.extend(row for row in parsed if row["JurisdictionCode"] == country_code)
    if not rows:
        raise RuntimeError(f"[NZ-DHB] No PHF annual observations for {country_code}")
    return NZUpdateFetchResult(rows=[], source_latest_date=max(date.fromisoformat(row["Date"]) for row in rows), source_csv=Path(output_csv), script_logs=[], series_rows=rows)
