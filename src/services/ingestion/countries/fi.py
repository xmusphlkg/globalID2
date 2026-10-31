"""THL cube transport and offline dimension-preserving Cases parsing."""
import asyncio
import requests
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.fi import DEFAULT_SCOPE, FinlandTHLCrawler, parse_dimension_catalog, parse_monthly_csv, FICubeDataError
from src.data.processors.fi import FIUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from .monthly_common import normalized_csv


async def collect(writer, *, source, months=None, output_csv, include_current_month=False, backfill_history=False, as_of=None, refresh_recent_months=3):
    if str(source or "").strip().casefold().replace("-", "_") not in {"", "all", "fi", "finland", "thl", "ttr", DEFAULT_SCOPE}:
        raise ValueError(f"Unsupported FI source: {source}")
    from src.data.processors.fi import FIMonthlyUpdater
    updater = FIMonthlyUpdater(output_csv=Path(output_csv), include_current_month=include_current_month, refresh_recent_months=refresh_recent_months)
    today = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or datetime.now(timezone.utc).date()
    requested = updater._resolve_requested_months(months, as_of=today, include_provisional=include_current_month, backfill_history=backfill_history)
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv", metadata={"requested": requested})
    crawler = FinlandTHLCrawler(save_raw=False)
    try:
        with capture_writer(writer) as capture:
            try:
                catalog = await asyncio.to_thread(crawler.discover_dimensions)
            except requests.RequestException:
                capture.verify()
                prior = [item for item in writer.artifacts if item.kind == "prior_csv"]
                if prior:
                    return prior
                raise
            capture.verify()
        await writer.write(crawler._dimension_payload, source_url=crawler._dimension_response_url, kind="thl_dimensions", metadata={"as_of": today.isoformat(), "requested": requested})
        requested, omitted = crawler._resolve_months(catalog, months=requested, backfill_history=backfill_history, include_provisional=include_current_month, as_of=today)
        fetched_at = datetime.now(timezone.utc).isoformat()
        for node in crawler._query_nodes(catalog, requested, as_of=today, include_provisional=include_current_month):
            try:
                response = await asyncio.to_thread(crawler.get, crawler.csv_url, params=crawler._query_params(catalog, node))
            except requests.RequestException:
                prior = [item for item in writer.artifacts if item.kind == "prior_csv"]
                if prior:
                    return prior
                raise
            await writer.write(response.content, source_url=str(response.url), kind="thl_csv", metadata={"as_of": today.isoformat(), "requested": requested, "retrieved_at": fetched_at, "source_updated_at": response.headers.get("last-modified", "")})
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv, include_current_month=False, backfill_history=False, as_of=None, refresh_recent_months=3):
    today = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or datetime.now(timezone.utc).date()
    requested = updater._resolve_requested_months(months, as_of=today, include_provisional=include_current_month, backfill_history=backfill_history)
    dimensions = next((item for item in artifacts if item.kind == "thl_dimensions"), None)
    catalog = parse_dimension_catalog(read_artifact(dimensions).decode("utf-8-sig")) if dimensions else None
    live, prior = {}, []
    for item in artifacts:
        if item.kind == "prior_csv":
            prior = updater.parse_source_csv(read_artifact(item))
        elif item.kind == "thl_csv":
            meta = item.metadata_
            rows = parse_monthly_csv(read_artifact(item).decode("utf-8-sig"), catalog, requested_months=requested, as_of=today, query_url=item.source_url, retrieved_at=datetime.fromisoformat(meta["retrieved_at"]), response_sha256=item.sha256, source_updated_at=meta.get("source_updated_at", ""))
            for row in rows:
                key = (row["Date"], row["DiseaseCode"])
                if key in live and live[key] != row:
                    raise FICubeDataError(f"Conflicting THL rows returned across cube queries for {key}")
                live[key] = row
    live_rows = updater.parse_source_csv(normalized_csv(live.values())) if live else []
    prior = updater._filter_rows_for_months(prior, requested) if updater._rows_cover_months(prior, requested) else []
    rows = live_rows if updater._rows_cover_months(live_rows, requested) else prior
    if not rows:
        raise RuntimeError("FI THL crawler produced no complete monthly rows")
    return FIUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), [])
