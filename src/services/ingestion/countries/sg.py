"""CDA source discovery/downloads and offline CSV/XLSX/PDF normalization."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.sg import (
    HISTORY_START_YEAR,
    OPEN_DATA_POLL_URL,
    YEAR_PAGE,
    SGContractError,
    SingaporeCDACrawler,
    parse_annual_workbook,
    parse_historical_csv,
    parse_weekly_pdf,
)
from src.data.processors.sg import SGUpdateFetchResult, _date, _text
from src.services.ingestion.artifacts import read_artifact


def validate_source(source):
    if str(source).strip().lower() not in {
        "all",
        "sg",
        "singapore",
        "cda",
        "widb",
        "cda_weekly_bulletin",
    }:
        raise ValueError(f"Unsupported SG source: {source!r}")


async def collect(
    writer,
    *,
    source,
    force=False,
    fill_missing=False,
    start_year=HISTORY_START_YEAR,
    history_csv=None,
    **options,
):
    validate_source(source)
    crawler = SingaporeCDACrawler()
    current_year = datetime.now(timezone.utc).year
    full = bool(force or fill_missing)
    years = (
        range(2023, current_year + 1)
        if full
        else sorted({max(2024, current_year - 1), current_year})
    )
    try:
        if full:
            response = await asyncio.to_thread(crawler.get, OPEN_DATA_POLL_URL)
            await writer.write(
                response.content,
                source_url=OPEN_DATA_POLL_URL,
                kind="archive_json",
                metadata={"endpoint": "history_poll"},
            )
            poll = json.loads(response.content)
            url = (
                (poll.get("data") or {}).get("url") if isinstance(poll, dict) else None
            )
            if not url:
                raise SGContractError(
                    "data.gov.sg poll-download response has no data.url"
                )
            response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=url, kind="historical_csv")
        for year in years:
            workbook, pdfs, page = await asyncio.to_thread(
                crawler.discover_year_assets, year
            )
            await writer.write(
                page,
                source_url=YEAR_PAGE.format(year=year),
                kind="html_report",
                metadata={"year": year, "pdf_weeks": [week for week, _ in pdfs]},
            )
            workbook_saved = False
            if workbook:
                try:
                    response = await asyncio.to_thread(crawler.get, workbook)
                except Exception:
                    if year != 2023:
                        raise
                else:
                    await writer.write(
                        response.content,
                        source_url=workbook,
                        kind="cda_xlsx",
                        metadata={"year": year},
                    )
                    workbook_saved = True
            if year == 2023:
                # Capture fallbacks before organize so a damaged 2023 workbook
                # can fall back without making a source request from the parser.
                for week, url in pdfs:
                    try:
                        response = await asyncio.to_thread(crawler.get, url)
                    except Exception:
                        if not workbook_saved:
                            raise
                        continue
                    await writer.write(
                        response.content,
                        source_url=url,
                        kind="cda_pdf",
                        metadata={"year": year, "week": week},
                    )
            elif not workbook:
                raise SGContractError(f"No usable CDA annual source for {year}")
        if history_csv and Path(history_csv).exists():
            await writer.write(
                await asyncio.to_thread(Path(history_csv).read_bytes),
                source_url=Path(history_csv).as_posix(),
                kind="history_csv",
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(
    artifacts,
    *,
    updater,
    source,
    force=False,
    fill_missing=False,
    start_year=HISTORY_START_YEAR,
    **options,
):
    validate_source(source)
    fresh = []
    merged = {}
    annual = {}
    pdfs = {}
    expected_pdf_weeks = {}
    years = set()
    for artifact in artifacts:
        raw = read_artifact(artifact)
        if artifact.kind == "history_csv":
            for rawrow in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
                row = {str(key): _text(value) for key, value in rawrow.items()}
                if row.get("Date") and row.get("SourceDiseaseCode"):
                    merged[(row["Date"], row["SourceDiseaseCode"])] = row
        elif artifact.kind == "historical_csv":
            fresh.extend(parse_historical_csv(raw, source_url=OPEN_DATA_POLL_URL))
        elif artifact.kind == "html_report":
            years.add(artifact.metadata_["year"])
            expected_pdf_weeks[artifact.metadata_["year"]] = set(
                artifact.metadata_.get("pdf_weeks", [])
            )
        elif artifact.kind == "cda_xlsx":
            annual[artifact.metadata_["year"]] = (raw, artifact.source_url)
        elif artifact.kind == "cda_pdf":
            pdfs.setdefault(artifact.metadata_["year"], []).append(
                (artifact.metadata_["week"], raw, artifact.source_url)
            )
    for year in sorted(years | set(annual) | set(pdfs)):
        if year in annual:
            raw, url = annual[year]
            try:
                fresh.extend(parse_annual_workbook(raw, year=year, source_url=url))
                continue
            except Exception:
                if year != 2023:
                    raise
        if year != 2023 or not pdfs.get(year):
            raise SGContractError(f"No usable CDA annual source for {year}")
        expected = expected_pdf_weeks.get(year, set())
        if expected and {week for week, _, _ in pdfs[year]} != expected:
            raise SGContractError(f"Incomplete CDA PDF fallback for {year}")
        for week, raw, url in sorted(pdfs[year]):
            fresh.extend(parse_weekly_pdf(raw, year=year, week=week, source_url=url))
    for row in fresh:
        merged[(row["Date"], row["SourceDiseaseCode"])] = row
    all_rows = sorted(
        merged.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"])
    )
    latest = max((date.fromisoformat(row["Date"]) for row in all_rows), default=None)
    fields = {key for row in all_rows for key in row}
    rows = [
        {key: _text(row.get(key, "")) for key in fields}
        for row in all_rows
        if _date(row) is not None and _text(row.get("Cases")) != ""
    ]
    if force or fill_missing:
        first = max(
            HISTORY_START_YEAR, min(datetime.now(timezone.utc).year, int(start_year))
        )
        rows = [row for row in rows if int(row.get("Year") or 0) >= first]
    else:
        recent = sorted({_date(row) for row in rows}, reverse=True)[
            : updater.refresh_recent_weeks
        ]
        rows = [row for row in rows if _date(row) in set(recent)]
    return SGUpdateFetchResult(
        rows,
        latest,
        updater.output_csv,
        [
            f"[artifacts] {len(artifacts)} CDA source files; {len(rows)} weekly observations"
        ],
    )
