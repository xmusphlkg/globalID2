"""Audited metadata-only gate for Colombia's large historical SIVIGILA release."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.co import (
    COUNT_URL, DEFAULT_SCOPE, HISTORY_START_YEAR, METADATA_URL,
    ColombiaINSSivigilaCrawler, ColombiaSIVIGILAContractError,
    can_skip_import, parse_metadata,
)
from src.services.ingestion.artifacts import read_artifact

ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = ROOT / "data/current/co/history-state.json"
ACCEPTANCE_PATH = ROOT / "data/current/co/database_acceptance.json"
YEAR_COUNT_QUERY = {"$select": "ano,count(*) as n", "$group": "ano", "$order": "ano"}


class ColombiaHistoryRefreshRequired(RuntimeError):
    """The scheduled check cannot replace the version-bound historical loader."""


def parse_year_counts(payload: bytes) -> dict[str, int]:
    try:
        document = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ColombiaSIVIGILAContractError("Invalid official grouped year counts") from exc
    if not isinstance(document, list) or not document:
        raise ColombiaSIVIGILAContractError("Official grouped year counts are empty")
    counts: dict[str, int] = {}
    for row in document:
        if not isinstance(row, dict) or not str(row.get("ano", "")).isdigit() or not str(row.get("n", "")).isdigit():
            raise ColombiaSIVIGILAContractError("Invalid official grouped year count row")
        year, count = int(row["ano"]), int(row["n"])
        if year < HISTORY_START_YEAR or year > datetime.now(timezone.utc).year or count <= 0 or str(year) in counts:
            raise ColombiaSIVIGILAContractError("Duplicate or out-of-range official grouped year")
        counts[str(year)] = count
    return dict(sorted(counts.items()))


async def collect(writer, *, source=DEFAULT_SCOPE, crawler_type=ColombiaINSSivigilaCrawler, **options):
    from src.data.processors.co import ColombiaINSSivigilaUpdater
    ColombiaINSSivigilaUpdater.normalize_source(source)
    crawler = crawler_type()
    try:
        for endpoint, url, params in (
            ("metadata_before", METADATA_URL, None),
            ("grouped_year_counts", COUNT_URL, YEAR_COUNT_QUERY),
            ("metadata_after", METADATA_URL, None),
        ):
            response = await asyncio.to_thread(crawler.get, url, params=params)
            await writer.write(response.content, source_url=response.url, kind="archive_json",
                               metadata={"endpoint": endpoint,
                                         "retrieved_at": datetime.now(timezone.utc).isoformat()})
        return writer.artifacts
    finally:
        crawler.session.close()


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ColombiaSIVIGILAContractError(f"Expected JSON object at {path}")
    return value


def _backfill_message(counts: dict[str, int], reason: str) -> str:
    return (
        f"requires_full_backfill: {reason}. Resume the complete version-bound import with "
        f"`venv/bin/python scripts/imports/co_sivigila_history.py --start-year {HISTORY_START_YEAR} "
        f"--end-year {max(map(int, counts))} --apply`, then run "
        "`venv/bin/python scripts/verify_co_sivigila_history.py --require-complete`. "
        "A single source page is not a successful refresh."
    )


def organize(artifacts, *, updater, source=DEFAULT_SCOPE,
             state_path=STATE_PATH, acceptance_path=ACCEPTANCE_PATH, **options):
    updater.normalize_source(source)
    captured = {}
    for artifact in artifacts:
        endpoint = artifact.metadata_.get("endpoint")
        if endpoint in {"metadata_before", "grouped_year_counts", "metadata_after"}:
            if endpoint in captured:
                raise ColombiaSIVIGILAContractError(f"Duplicate {endpoint} gate artifact")
            captured[endpoint] = artifact
    if set(captured) != {"metadata_before", "grouped_year_counts", "metadata_after"}:
        raise ColombiaSIVIGILAContractError("Three captured official metadata gate artifacts required")
    if (captured["metadata_before"].source_url != METADATA_URL
            or captured["metadata_after"].source_url != METADATA_URL
            or not captured["grouped_year_counts"].source_url.startswith(COUNT_URL)):
        raise ColombiaSIVIGILAContractError("Gate artifact source URL changed")
    before = parse_metadata(read_artifact(captured["metadata_before"]))
    counts = parse_year_counts(read_artifact(captured["grouped_year_counts"]))
    after = parse_metadata(read_artifact(captured["metadata_after"]))
    if before != after:
        raise ColombiaHistoryRefreshRequired(_backfill_message(counts, "INS SIVIGILA changed during metadata polling"))
    state = _read_json(Path(state_path))
    acceptance = _read_json(Path(acceptance_path))
    if options.get("force") or not can_skip_import(before, counts, state, acceptance):
        raise ColombiaHistoryRefreshRequired(
            _backfill_message(counts, "INS SIVIGILA history is incomplete, forced, or its version/year counts changed"))
    audit = {"status": "unchanged_verified_metadata_only", "source_url": METADATA_URL,
             "dataset_version": before, "year_counts": counts,
             "source_rows": sum(counts.values()), "history_state": "complete",
             "independent_database_acceptance": True, "downloaded_case_pages": 0,
             "public_release_enabled": False}
    from src.data.processors.co import ColombiaFetchResult
    return ColombiaFetchResult([], None, updater.output_csv,
                               ["[CO] Official metadata and year counts unchanged; verified history already stored"],
                               audit)
