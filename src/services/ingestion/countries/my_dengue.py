"""Durably capture official iDengue HTML, then normalize without network access."""
import asyncio
from datetime import datetime, timezone

from src.data.crawlers.my_dengue import (
    DEFAULT_SCOPE,
    LANDING_URL,
    MalaysiaIDengueCrawler,
    parse_idengue_html,
)
from src.data.processors.my_dengue import MalaysiaIDengueDailyUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, **options):
    MalaysiaIDengueDailyUpdater.normalize_source(source)
    crawler = MalaysiaIDengueCrawler()
    try:
        response = await asyncio.to_thread(crawler.get, LANDING_URL)
        await writer.write(response.content, source_url=response.url, kind="my_idengue_html",
                           metadata={"retrieved_at": datetime.now(timezone.utc).isoformat()})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    releases = [artifact for artifact in artifacts if artifact.kind == "my_idengue_html"]
    if len(releases) != 1:
        raise ValueError("Exactly one captured iDengue daily HTML release is required")
    artifact = releases[0]
    retrieved = artifact.metadata_.get("retrieved_at")
    if not retrieved:
        raise ValueError("Captured iDengue retrieval time is required for deterministic replay")
    parsed = parse_idengue_html(read_artifact(artifact), retrieved_at=retrieved)
    return updater.prepare_rows(parsed.rows, source=source, source_audit=parsed.source_audit, **options)
