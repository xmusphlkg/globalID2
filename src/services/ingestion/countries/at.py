"""AGES source issue discovery and offline authority CSV parsing."""
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.at import DEFAULT_SCOPE, AustriaAGESRadarCrawler, AGESIssue, parse_ages_csv
from src.data.processors.at import ATUpdateFetchResult, _parse_date
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from .monthly_common import normalized_csv


async def collect(writer, *, source, months=None, output_csv, force=False, refresh_recent_months=3):
    if str(source or "").strip().casefold() not in {"all", "at", "austria", "ages", DEFAULT_SCOPE}:
        raise ValueError(f"Unsupported AT source: {source!r}")
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv")
    crawler = AustriaAGESRadarCrawler(save_raw=False)
    try:
        # Issue headers are transport discovery: they identify which document
        # belongs to the requested reporting window, before disease parsing.
        with capture_writer(writer):
            issues = await asyncio.to_thread(crawler.discover_issues)
        for issue in issues:
            if not force and months is not None and (issue.report_month.year, issue.report_month.month) not in set(map(tuple, months)):
                continue
            response = await asyncio.to_thread(crawler.get, issue.csv_url)
            await writer.write(response.content, source_url=issue.csv_url, kind="ages_csv", metadata={"detail_url": issue.detail_url, "report_month": issue.report_month.isoformat(), "retrieved_at": issue.retrieved_at.isoformat()})
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv, force=False, refresh_recent_months=3):
    targets = list(months) if months is not None else updater._recent_months(datetime.now(timezone.utc).date(), refresh_recent_months)
    merged = {}
    for artifact in artifacts:
        raw = read_artifact(artifact)
        if artifact.kind == "prior_csv":
            rows = updater.parse_source_csv(raw)
        elif artifact.kind == "ages_csv":
            meta = artifact.metadata_
            issue = AGESIssue(meta["detail_url"], artifact.source_url, date.fromisoformat(meta["report_month"]), datetime.fromisoformat(meta["retrieved_at"]))
            rows = parse_ages_csv(raw, issue=issue)
        else:
            continue
        for row in rows:
            merged[(row["Date"], row["SourceDiseaseCode"])] = row
    rows = updater.parse_source_csv(normalized_csv(sorted(merged.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"])), add_disease=False)) if merged else []
    if not force:
        rows = [row for row in rows if (parsed := _parse_date(row)) and (parsed.year, parsed.month) in set(map(tuple, targets))]
    latest = max((_parse_date(row) for row in merged.values()), default=None)
    return ATUpdateFetchResult(rows, latest, Path(output_csv), ["[gate] public release disabled pending AGES license review"])
