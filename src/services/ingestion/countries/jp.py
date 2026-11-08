"""JIHS IDWR: transport discovery followed by offline CSV organization."""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, timedelta
from pathlib import Path

from src.core import get_logger
from src.data.crawlers.jp import JapanIDWRCrawler, _norm_text
from src.data.processors.jp import JPUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact

logger = get_logger(__name__)


async def collect(writer, *, source, output_csv, force=False, fill_missing=False, incremental_cache_weeks=None):
    crawler = JapanIDWRCrawler()
    try:
        path = Path(output_csv)
        if path.exists() and (not force or source == "local"):
            await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.as_posix(), kind="standardized_cache", metadata={"filename": path.name})
        if source == "local":
            if not path.exists():
                raise FileNotFoundError(path)
            return writer.artifacts
        existing_weeks = await asyncio.to_thread(crawler._load_existing_year_weeks, path)
        if crawler.explicit_csv_url:
            urls = [crawler.explicit_csv_url]
        else:
            from src.services.ingestion.capture import capture_writer
            with capture_writer(writer):
                urls, _, _ = await asyncio.to_thread(crawler._discover_weekly_csv_urls, existing_weeks=existing_weeks, force=force, fill_missing=fill_missing)
        if not urls and force and path.exists():
            # The source's force/no-new-URLs contract retains the existing
            # standardized snapshot. A force run with URLs replaces it.
            await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.as_posix(), kind="standardized_cache", metadata={"filename": path.name})
        failures = []
        for url in urls:
            try:
                response = await asyncio.to_thread(crawler.get, url)
            except Exception as exc:
                failures.append(str(exc))
                continue
            # Storage failures are fatal, never a source-download fallback.
            await writer.write(response.content, source_url=url, kind="weekly_csv", metadata={"source_kind": crawler._csv_kind_from_url(url)})
        if urls and len(failures) == len(urls):
            raise RuntimeError("JP source refresh failed: " + "; ".join(failures[:3]))
        if failures:
            logger.warning("JP source refresh skipped {} of {} CSV files: {}", len(failures), len(urls), "; ".join(failures[:3]))
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source, output_csv, force=False, fill_missing=False, incremental_cache_weeks=None):
    crawler = JapanIDWRCrawler()
    merged = {}
    cache_rows_skipped = 0
    cache_cutoff = None
    try:
        for artifact in artifacts:
            payload = read_artifact(artifact)
            if artifact.kind == "standardized_cache":
                # The historical cache can contain millions of rows. Feed the
                # deduplication map directly instead of retaining a second list
                # of all of its dictionaries at the same time.
                stream = io.StringIO(payload.decode("utf-8-sig"))
                cutoff = None
                if incremental_cache_weeks and not force and source != "local":
                    latest_week = None
                    for entry in csv.DictReader(stream):
                        try:
                            week = date.fromisocalendar(int(entry["Current MMWR Year"]), int(entry["MMWR WEEK"]), 1)
                        except (KeyError, ValueError, TypeError):
                            continue
                        if latest_week is None or week > latest_week:
                            latest_week = week
                    if latest_week:
                        cutoff = latest_week - timedelta(weeks=max(1, int(incremental_cache_weeks)) - 1)
                        cache_cutoff = cutoff
                    stream.seek(0)
                rows = csv.DictReader(stream)
                priority = 2
                default_surveillance = "unspecified"
            elif artifact.kind == "weekly_csv":
                text = None
                for encoding in ("utf-8-sig", "cp932", "shift_jis", "euc_jp"):
                    try:
                        text = payload.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        pass
                if text is None:
                    raise RuntimeError(f"Unable to decode CSV content: {artifact.source_url}")
                kind = artifact.metadata_["source_kind"]
                rows = crawler._normalize_rows(list(csv.reader(text.splitlines())), reporting_area="ALL", source_kind=kind)
                priority = 0 if kind == "zensu" else 1
                default_surveillance = "all_case" if kind == "zensu" else "sentinel" if kind == "teiten" else "unspecified"
            else:
                continue
            for row in rows:
                if artifact.kind == "standardized_cache" and cutoff is not None:
                    try:
                        row_week = date.fromisocalendar(int(row["Current MMWR Year"]), int(row["MMWR WEEK"]), 1)
                    except (KeyError, ValueError, TypeError):
                        continue
                    if row_week < cutoff:
                        cache_rows_skipped += 1
                        continue
                row["SurveillanceType"] = _norm_text(row.get("SurveillanceType")) or default_surveillance
                key = tuple(row.get(field, "") for field in ("Reporting Area", "Current MMWR Year", "MMWR WEEK", "Disease", "SurveillanceType"))
                if all(key) and (key not in merged or priority < merged[key][0]):
                    merged[key] = (priority, row)
            # Drop the CSV reader and its decoded buffer before allocating the
            # normalized output. The merge map retains only its selected rows.
            del rows, payload
            if artifact.kind == "weekly_csv":
                del text
            else:
                del stream
        if not merged:
            raise RuntimeError("JP crawler found no valid rows in collected artifacts")
        rows = updater.normalize_standardized_rows((row for _, row in merged.values()), source_name=Path(output_csv).name)
        if updater.country_code != updater.series_country_code:
            rows = [row for row in rows if row.get("GeographyKey") == f"country:{updater.country_code}:national"]
        logs = []
        if cache_cutoff:
            logs.append(f"[incremental] cache weeks since {cache_cutoff.isoformat()}; skipped {cache_rows_skipped} older cached rows; downloaded weeks retained")
            logger.info("JP {}", logs[-1])
        return JPUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), logs)
    finally:
        crawler.session.close()
