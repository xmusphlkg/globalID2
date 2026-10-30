"""PHAC CNDSS transports full source coverage and parses captured bytes offline."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from src.data.crawlers.ca_national import (
    CNDSS_DISEASE_CONTRACTS,
    DEFAULT_SCOPE,
    DESCRIBE_URL,
    EXPECTED_FIRST_YEAR,
    EXPECTED_LAST_YEAR,
    RAW_URL,
    CanadaCNDSSNationalCrawler,
    CNDSSContractError,
)
from src.data.processors.ca_national import CNDSSUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact


def normalize_source(source):
    if str(source).strip().lower() not in {
        "all",
        "ca",
        "canada",
        "cndss",
        "phac",
        DEFAULT_SCOPE,
    }:
        raise ValueError(f"Unsupported Canada CNDSS source: {source!r}")


async def collect(writer, *, source=DEFAULT_SCOPE, **options):
    normalize_source(source)
    crawler = CanadaCNDSSNationalCrawler()
    try:
        response = await asyncio.to_thread(crawler.get, DESCRIBE_URL)
        await writer.write(
            response.content,
            source_url=response.url,
            kind="archive_json",
            metadata={"endpoint": "describe"},
        )
        codes = ",".join(str(item.code) for item in CNDSS_DISEASE_CONTRACTS)
        response = await asyncio.to_thread(
            crawler.get,
            RAW_URL,
            params=[
                ("s", str(EXPECTED_FIRST_YEAR)),
                ("f", f"d:in:{codes}"),
                ("f", f"y:..:{EXPECTED_FIRST_YEAR},{EXPECTED_LAST_YEAR}"),
            ],
        )
        await writer.write(
            response.content,
            source_url=response.url,
            kind="archive_json",
            metadata={"endpoint": "raw"},
        )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=1924, **options):
    normalize_source(source)
    payloads = {
        artifact.metadata_["endpoint"]: json.loads(read_artifact(artifact))
        for artifact in artifacts
    }
    if set(payloads) != {"describe", "raw"}:
        raise CNDSSContractError("CNDSS describe and raw artifacts are required")
    first, last = CanadaCNDSSNationalCrawler._validate_describe(payloads["describe"])
    if max(first, int(start_year)) > last:
        raise CNDSSContractError("Requested CNDSS start year is after source coverage")
    # Keep the reviewed complete grid, including older history. Replacement is
    # bounded independently by the CNDSS commit strategy, never by this parser.
    rows = CanadaCNDSSNationalCrawler._rows_from_payload(
        payloads["raw"], first_year=first, last_year=last
    )
    latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
    return CNDSSUpdateFetchResult(
        rows,
        latest,
        updater.output_csv,
        [
            f"[artifacts] {len(artifacts)} captured responses; {len(rows)} national CNDSS annual observations"
        ],
    )
