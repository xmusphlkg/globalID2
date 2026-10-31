"""SmiNet page/CSV transport with offline county-grain normalization."""
import asyncio
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.se import SOURCE_SCOPE, SwedenSmiNetCrawler, SEDiseasePage, closed_months, available_monthly_years, parse_monthly_html_geographies, parse_monthly_html, parse_monthly_csv_geographies, _publication_boundary
from src.data.processors.se import SEMonthlyUpdater, SEUpdateFetchResult
from src.data.crawlers.subdivision_registry import source_label_for_code
from src.services.ingestion.artifacts import read_artifact
from .monthly_common import normalized_csv


def _today(value):
    return date.fromisoformat(value) if isinstance(value, str) else value or datetime.now(timezone.utc).date()


async def collect(writer, *, source, months=None, output_csv, country_code="SE", include_current_month=False, force=False, today=None, refresh_recent_months=3):
    if str(source or "").strip().casefold() not in {"all", SOURCE_SCOPE, "fhm_sminet", "fohm", "fhm", "sminet", "se"}:
        raise ValueError(f"Unsupported Sweden source: {source}")
    effective = _today(today)
    planner = SEMonthlyUpdater(country_code=country_code, output_csv=Path(output_csv), include_current_month=include_current_month, refresh_recent_months=refresh_recent_months)
    requested = planner._resolve_requested_months(months, today=effective, include_current_month=include_current_month)
    if not requested:
        raise ValueError("[SE-FHM] No eligible calendar months were requested")
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv")
    crawler = SwedenSmiNetCrawler(save_raw=False)
    try:
        response = await asyncio.to_thread(crawler._get_response, crawler.index_url)
        await writer.write(response.content, source_url=crawler.index_url, kind="smi_index", metadata={"encoding": response.encoding or "utf-8"})
        pages = crawler.discover_disease_pages(response.text)
        if not pages:
            raise RuntimeError("[SE-FHM] No matching statistics pages discovered")
        years = sorted({year for year, _ in requested}, reverse=True)
        retrieved = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for page in pages:
            available = None
            for year in years:
                if available is not None and year not in available:
                    continue
                url = crawler.page_url_for_year(page, year)
                try:
                    response = await asyncio.to_thread(crawler._get_response, url)
                except Exception:
                    continue
                html = response.text
                await writer.write(response.content, source_url=str(response.url), kind="smi_html", metadata={"page": asdict(page), "year": year, "encoding": response.encoding or "utf-8", "retrieved_at": retrieved})
                if year == years[0]:
                    options = available_monthly_years(html)
                    if options:
                        available = options
                _, csv_url, updated = crawler._page_metadata(html, page=page, year=year, page_url=str(response.url))
                if csv_url:
                    try:
                        raw = await asyncio.to_thread(crawler._fetch_csv_bytes, csv_url)
                    except Exception:
                        continue
                    await writer.write(raw, source_url=csv_url, kind="smi_csv", metadata={"code": page.code, "year": year})
    finally:
        crawler.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv, country_code="SE", include_current_month=False, force=False, today=None, refresh_recent_months=3):
    effective = _today(today)
    requested = set(updater._resolve_requested_months(months, today=effective, include_current_month=include_current_month))
    provisional = {key for key in requested if key > _publication_boundary(effective)}
    csvs = {(item.metadata_["code"], item.metadata_["year"]): item for item in artifacts if item.kind == "smi_csv"}
    prior = []
    source_rows = []
    updates = []
    for item in artifacts:
        raw = read_artifact(item)
        if item.kind == "prior_csv":
            prior = updater._filter_rows_for_months(updater.parse_source_csv(raw), requested)
        elif item.kind == "smi_html":
            meta = item.metadata_
            page, year = SEDiseasePage(**meta["page"]), meta["year"]
            html = raw.decode(meta.get("encoding") or "utf-8")
            label, csv_url, updated = SwedenSmiNetCrawler._page_metadata(html, page=page, year=year, page_url=item.source_url)
            html_geographies = parse_monthly_html_geographies(html, year)
            totals = html_geographies.get("Totalt") or parse_monthly_html(html, year)
            if not totals:
                continue
            geographies, method = {}, "html_fallback"
            csv_artifact = csvs.get((page.code, year))
            if csv_artifact:
                try:
                    geographies = parse_monthly_csv_geographies(read_artifact(csv_artifact))
                    if geographies:
                        method = "machine_csv"
                except Exception:
                    geographies = {}
            geographies = geographies or html_geographies or {"Totalt": totals}
            rows = []
            for geography, values in geographies.items():
                rows.extend(SwedenSmiNetCrawler._normalized_rows(page=page, disease_label=label, year=year, totals=values, geography="SE:national" if geography.casefold() == "totalt" else f"SE:county:{geography}", requested_months=requested, provisional_months=provisional, page_url=item.source_url, csv_url=csv_url, source_updated_at=updated, retrieved_at=meta["retrieved_at"], retrieval_method=method))
            source_rows.extend(rows)
            if rows and updated:
                updates.append(updated)
    placeholders = {key for key in provisional if not any(int(row["Cases"]) > 0 for row in source_rows if (int(row["Year"]), int(row["Month"])) == key)}
    source_rows = [row for row in source_rows if (int(row["Year"]), int(row["Month"])) not in placeholders]
    prior = [row for row in prior if (int(row["Year"]), int(row["Month"])) not in placeholders]
    live = updater.parse_source_csv(normalized_csv(source_rows)) if source_rows else []
    if not live and not prior:
        raise RuntimeError("SE SmiNet crawler produced no usable rows")
    rows, _ = updater._merge_live_with_cache(live, prior)
    if updater.country_code != updater.series_country_code:
        label = source_label_for_code(updater.series_country_code, updater.country_code)
        rows = [row for row in rows if label and row.get("Geography") == f"SE:county:{label}"]
    return SEUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), [], source_updated_at=max(updates, default=None))
