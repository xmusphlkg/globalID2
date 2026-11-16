"""Capture and replay the full MOH annual notified-disease release."""
import asyncio
from datetime import datetime, timezone

from src.data.crawlers.my_notifiable import DATA_URL, DEFAULT_SCOPE, MalaysiaMOHNotifiableCrawler, parse_moh_annual_payload
from src.data.processors.my_notifiable import MYAnnualUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, **options):
    MYAnnualUpdater.normalize_source(source)
    crawler = MalaysiaMOHNotifiableCrawler()
    try:
        response = await asyncio.to_thread(crawler.get, DATA_URL)
        await writer.write(response.content, source_url=response.url, kind="moh_notifiable_parquet", metadata={"retrieved_at": datetime.now(timezone.utc).isoformat()})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    releases = [artifact for artifact in artifacts if artifact.kind == "moh_notifiable_parquet"]
    if len(releases) != 1:
        raise ValueError("Exactly one MOH annual notifications release is required")
    artifact = releases[0]
    rows = parse_moh_annual_payload(read_artifact(artifact), retrieved_at=artifact.metadata_.get("retrieved_at"))
    return updater.prepare_rows(rows, source=source, **options)
