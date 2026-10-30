"""PHF report transport followed by offline National/Rolling workbook parsing."""
import asyncio
import csv
import io
import requests
from pathlib import Path
from tempfile import TemporaryDirectory

from src.data.crawlers.nz import NewZealandPHFCrawler
from src.data.processors.nz import NZMonthlyUpdater, NZUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from src.services.ingestion.replay import offline_crawler_type
from .monthly_refresh import merge_monthly_refresh


async def collect(writer, *, source, months=None, output_csv):
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv")
    updater = NZMonthlyUpdater(output_csv=path)
    requested = set(updater._resolve_requested_months(months))
    crawler = NewZealandPHFCrawler(save_raw=False)
    try:
        with capture_writer(writer) as capture:
            try:
                reports = await asyncio.to_thread(crawler.fetch_report_index, max_pages=10)
            except requests.RequestException:
                capture.verify()
                raise
            capture.verify()
            if not reports:
                raise RuntimeError("[NZ] No reports found in Digital Library index")
            for report in reports:
                if report.get("type") == "individual":
                    coverage = set()
                    for delta in range(12):
                        year, month = report["year"], report["month"] - delta
                        if month <= 0:
                            year, month = year - 1, month + 12
                        coverage.add((year, month))
                    if not requested.intersection(coverage):
                        continue
                elif report.get("type") == "yearly_archive":
                    if report["year"] not in {year for year, _ in requested}:
                        continue
                else:
                    continue
                url = await asyncio.to_thread(crawler.resolve_download_url, report)
                capture.verify()
                if not url:
                    continue
                try:
                    response = await asyncio.to_thread(crawler.get, url)
                except requests.RequestException:
                    capture.verify()
                    continue
                # The source index contains datetime.date; only transport
                # descriptors needed by offline parsing belong in JSON metadata.
                descriptor = {key: report[key] for key in ("type", "year", "month", "url", "title", "download_url") if key in report}
                await writer.write(response.content, source_url=url, kind="nz_report_zip", metadata={"report": descriptor, "media_type": "application/zip"})
    finally:
        crawler.session.close()
    if not any(item.kind == "nz_report_zip" for item in writer.artifacts):
        raise RuntimeError("NZ live collection produced no report archives; cached data is not a successful refresh")
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv):
    requested = updater._resolve_requested_months(months)
    prior = []
    for item in artifacts:
        if item.kind != "prior_csv":
            continue
        payload = read_artifact(item)
        # The monthly processor intentionally projects only national columns;
        # reject a misrouted annual/DHB cache before it can lose those labels.
        for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
            if str(row.get("Frequency") or "monthly").lower() != "monthly" or row.get("JurisdictionCode") not in {None, "", "NZ"} or row.get("GeographyKey") not in {None, "", "country:NZ:national"}:
                raise ValueError("NZ monthly cache contains a different frequency or geography")
        prior = updater.parse_source_csv(payload)
    archives = [item for item in artifacts if item.kind == "nz_report_zip"]
    crawler_type = offline_crawler_type(NewZealandPHFCrawler, artifacts)
    crawler = crawler_type(save_raw=False)
    reports = [item.metadata_["report"] for item in archives]
    crawler.fetch_report_index = lambda **kwargs: reports
    crawler.resolve_download_url = lambda report: next((item.source_url for item in archives if item.metadata_["report"] == report), None)
    live = []
    try:
        if archives:
            with TemporaryDirectory(prefix="globalid-nz-organize-") as directory:
                output = Path(directory) / Path(output_csv).name
                crawler.crawl_monthly_national(output, months=requested)
                live = updater.parse_source_csv(output.read_bytes(), source_file=Path(output_csv).name)
    finally:
        crawler.session.close()
    rows, audit = merge_monthly_refresh(live, prior, requested_months=requested, country_code="NZ")
    result = NZUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), [f"[organize] live_rows={audit['live_rows']} fallback_rows={audit['fallback_rows']}"], series_rows=rows)
    result.source_audit = audit
    return result
