"""CHP annual byte snapshots; independent offline national monthly aggregation."""
import asyncio
import requests
from pathlib import Path

from src.data.crawlers.hk import HongKongCHPCrawler, aggregate_annual_csv_rows, _decode_csv_response, _last_n_available_months, _parse_row_date
from src.data.processors.hk import HKUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from .monthly_common import normalized_csv
from .monthly_refresh import merge_monthly_refresh


async def collect(writer, *, source, months=None, output_csv):
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv")
    crawler = HongKongCHPCrawler(save_raw=False)
    try:
        years = sorted({year for year, _ in months}) if months is not None else crawler._default_years()
        for year in years:
            try:
                response = await asyncio.to_thread(crawler.get, crawler._csv_url(year))
            except requests.RequestException:
                continue
            await writer.write(response.content, source_url=crawler._csv_url(year), kind="annual_csv", metadata={"year": year, "expected_years": years, "refresh_recent_months": crawler.refresh_recent_months})
        if not any(item.kind == "annual_csv" for item in writer.artifacts):
            raise RuntimeError("HK live collection failed for every requested year; cached data is not a successful refresh")
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv):
    source_rows, prior_rows = [], []
    for artifact in artifacts:
        payload = read_artifact(artifact)
        if artifact.kind == "prior_csv":
            prior_rows = updater.parse_source_csv(payload)
        elif artifact.kind == "annual_csv":
            source_rows.extend(aggregate_annual_csv_rows(artifact.metadata_["year"], HongKongCHPCrawler._parse_csv_rows(_decode_csv_response(payload)), months=set(map(tuple, months)) if months is not None else None, source_url=artifact.source_url))
    selected_months = set(map(tuple, months)) if months is not None else set()
    if months is None:
        selected_months = _last_n_available_months(source_rows, count=next((item.metadata_.get("refresh_recent_months", 3) for item in artifacts if item.kind == "annual_csv"), 3))
        source_rows = [row for row in source_rows if (parsed := _parse_row_date(row)) is not None and (parsed.year, parsed.month) in selected_months]
    live = updater.parse_source_csv(normalized_csv(source_rows)) if source_rows else []
    rows, audit = merge_monthly_refresh(live, prior_rows, requested_months=selected_months, country_code="HK")
    expected_years = {year for item in artifacts for year in (getattr(item, "metadata_", None) or {}).get("expected_years", [])}
    fetched_years = {item.metadata_["year"] for item in artifacts if item.kind == "annual_csv"}
    audit["missing_source_years"] = sorted(expected_years - fetched_years)
    if audit["missing_source_years"]:
        audit["status"] = "partial"
    result = HKUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), [f"[organize] live_rows={audit['live_rows']} fallback_rows={audit['fallback_rows']}"])
    result.source_audit = audit
    return result
