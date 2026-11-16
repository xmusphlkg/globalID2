"""Durably collect the complete RIVM annual table, then organize it offline."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from src.data.crawlers.nl_notifiable import (
    DEFAULT_SCOPE, HISTORY_START_YEAR, LANDING_URL, NetherlandsNotifiableCrawler,
    parse_notifiable_html,
)
from src.data.processors.nl_notifiable import NLNotifiableAnnualUpdater
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, output_csv=None, **options):
    NLNotifiableAnnualUpdater.normalize_source(source)
    crawler = NetherlandsNotifiableCrawler()
    try:
        response = await asyncio.to_thread(crawler.get, LANDING_URL)
        await writer.write(response.content, source_url=response.url, kind="rivm_notifiable_html", metadata={
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "as_of": date.today().isoformat(), "start_year": int(start_year),
        })
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR, **options):
    updater.normalize_source(source)
    releases = [artifact for artifact in artifacts if artifact.kind == "rivm_notifiable_html"]
    if len(releases) != 1:
        raise ValueError("Exactly one captured annual RIVM table is required")
    artifact = releases[0]
    # The acquisition fence is replayed, so a source that contains an open
    # calendar year cannot become acceptable merely because time has passed.
    rows = parse_notifiable_html(read_artifact(artifact),
        start_year=int(artifact.metadata_["start_year"]),
        as_of=date.fromisoformat(artifact.metadata_["as_of"]))
    return updater.prepare_rows(rows, source=source)
