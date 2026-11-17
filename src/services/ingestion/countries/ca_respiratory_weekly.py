"""Persist PHAC landing page, data binding and CSV before offline organization."""
import asyncio
from datetime import datetime, timezone

from src.data.crawlers.ca_respiratory_weekly import (
    DEFAULT_SCOPE, LANDING_URL, SCRIPT_URL, DATA_URL, CARespiratoryWeeklyCrawler, parse_csv,
)
from src.data.processors.ca_respiratory_weekly import CARespiratoryWeeklyUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, crawler_type=CARespiratoryWeeklyCrawler, **options):
    CARespiratoryWeeklyUpdater.normalize_source(source)
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        for endpoint, url in (("phac_landing", LANDING_URL), ("phac_script", SCRIPT_URL), ("phac_csv", DATA_URL)):
            response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=response.url,
                               kind="archive_json" if endpoint != "phac_csv" else "historical_csv",
                               metadata={"endpoint": endpoint, "requested_url": url, "retrieved_at": retrieved})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    captures = list(artifacts)
    expected = {"phac_landing": LANDING_URL, "phac_script": SCRIPT_URL, "phac_csv": DATA_URL}
    if len(captures) != 3 or {a.metadata_.get("endpoint") for a in captures} != set(expected):
        raise ValueError("PHAC source capture missing, duplicate or unexpected")
    if any(a.metadata_.get("requested_url") != expected[a.metadata_["endpoint"]] for a in captures):
        raise ValueError("PHAC captured endpoint differs from reviewed binding")
    dates = {a.metadata_["retrieved_at"] for a in captures}
    if len(dates) != 1:
        raise ValueError("PHAC source capture timestamps disagree")
    bodies = {a.metadata_["endpoint"]: read_artifact(a) for a in captures}
    if b"rvdss-figure1.js" not in bodies["phac_landing"] or b"RVD_WeeklyData.csv" not in bodies["phac_script"]:
        raise ValueError("PHAC page-to-script-to-CSV source binding changed")
    artifact = next(a for a in captures if a.metadata_["endpoint"] == "phac_csv")
    rows, audit = parse_csv(bodies["phac_csv"], as_of=datetime.fromisoformat(dates.pop().replace("Z", "+00:00")).date(),
                            provenance={"RawSHA256": artifact.sha256, "DownloadURL": artifact.source_url,
                                        "RetrievedAt": artifact.metadata_["retrieved_at"]})
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update(audit)
    return result
