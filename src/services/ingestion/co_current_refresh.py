"""Refresh the complete INS successor release, then reconcile its stored rows."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.co import ColombiaINSSivigilaCrawler, can_skip_import
from src.services.ingestion.artifacts import read_artifact

ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = ROOT / "data/current/co/current-state.json"
ACCEPTANCE_PATH = ROOT / "data/current/co/current_database_acceptance.json"
CATALOG_URL = ("https://api.us.socrata.com/api/catalog/v1?domains=www.datos.gov.co"
               "&for_user=issr-5hya&limit=100")
KNOWN_SIVIGILA_ASSETS = {"84d4-vfax", "xd2n-cu8j", "qvnt-2igj", "fhc4-jjti",
                        "4hyg-wa9d", "wns8-p38i"}


def _read_json(path):
    return json.loads(path.read_bytes()) if path.exists() else {}


def check_catalog(payload):
    """Detect another INS edition instead of silently watching an obsolete ID."""
    document = json.loads(payload)
    results = document.get("results")
    if not isinstance(results, list) or document.get("resultSetSize") != len(results):
        raise ValueError("Incomplete INS owner catalogue; cannot check for successor releases")
    candidates = []
    for item in results:
        resource = item["resource"]
        fields = set(resource.get("columns_field_name") or [])
        if ("sivigila" in resource.get("name", "").casefold()
                or {"cod_eve", "nombre_evento", "semana", "ano", "conteo"} <= fields):
            candidates.append(resource["id"])
    unknown = set(candidates) - KNOWN_SIVIGILA_ASSETS
    if unknown:
        raise RuntimeError("requires_new_source_contract: new official INS SIVIGILA asset(s): "
                           + ", ".join(sorted(unknown)))
    return {"owner_id": "issr-5hya", "assets_checked": len(results),
            "sivigila_assets": sorted(candidates), "new_source_assets": []}


async def _run(command, log_path):
    """Keep the controller and its import children in one cancellable group."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as stream:
        child = await asyncio.create_subprocess_exec(
            sys.executable, *map(str, command), cwd=ROOT,
            stdout=stream, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )
        try:
            code = await child.wait()
        except BaseException:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(child.wait(), timeout=20)
                except asyncio.TimeoutError:
                    os.killpg(child.pid, signal.SIGKILL)
                    await child.wait()
            raise
    if code:
        tail = log_path.read_bytes()[-3000:].decode("utf-8", errors="replace")
        raise RuntimeError(f"Complete CO current refresh failed ({code}); {log_path}: {tail}")


async def collect(writer, *, source="co_ins_sivigila_current", force=False, **options):
    from src.data.crawlers.co_current import (
        COUNT_URL,
        HISTORY_START_YEAR,
        METADATA_URL,
        parse_metadata,
    )
    from src.data.processors.co_current import ColombiaINSSivigilaCurrentUpdater
    from src.services.ingestion.co_update_gate import parse_year_counts

    ColombiaINSSivigilaCurrentUpdater.normalize_source(source)
    crawler = ColombiaINSSivigilaCrawler()
    captured = {}
    try:
        for endpoint, url, params in (
            ("metadata_before", METADATA_URL, None),
            ("grouped_year_counts", COUNT_URL,
             {"$select": "ano,count(*) as n", "$group": "ano", "$order": "ano"}),
            ("metadata_after", METADATA_URL, None),
            ("ins_owner_catalog", CATALOG_URL, None),
        ):
            response = await asyncio.to_thread(crawler.get, url, params=params)
            captured[endpoint] = response.content
            await writer.write(response.content, source_url=response.url, kind="archive_json",
                               metadata={"endpoint": endpoint,
                                         "retrieved_at": datetime.now(timezone.utc).isoformat()})
    finally:
        crawler.session.close()
    before = parse_metadata(captured["metadata_before"])
    if before != parse_metadata(captured["metadata_after"]):
        raise RuntimeError("INS successor release changed during publication check; retry")
    catalogue = check_catalog(captured["ins_owner_catalog"])
    counts = {year: count for year, count in parse_year_counts(captured["grouped_year_counts"]).items()
              if int(year) >= HISTORY_START_YEAR}
    if not counts:
        raise RuntimeError("INS successor release contains no supported years")
    state, acceptance = _read_json(STATE_PATH), _read_json(ACCEPTANCE_PATH)
    refreshed = bool(force or not can_skip_import(before, counts, state, acceptance))
    if refreshed:
        await _run([
            ROOT / "scripts/imports/co_sivigila_history.py", "--source", source,
            "--start-year", HISTORY_START_YEAR, "--end-year", datetime.now(timezone.utc).year,
            "--capture-workers", 3, "--page-limit", 20000, "--apply",
        ], writer.directory / "complete-refresh.log")
        await _run([
            ROOT / "scripts/verify_co_sivigila_history.py", "--source", source,
            "--require-complete",
        ], writer.directory / "complete-verification.log")
        state, acceptance = _read_json(STATE_PATH), _read_json(ACCEPTANCE_PATH)
        if not can_skip_import(before, counts, state, acceptance):
            raise RuntimeError("INS release changed during full refresh or final reconciliation failed; retry")
    audit = {
        "status": "complete_refreshed_verified" if refreshed else "unchanged_verified",
        "source": source, "dataset_version": before, "year_counts": counts,
        "source_rows": sum(counts.values()), "independent_database_acceptance": True,
        "source_catalogue": catalogue,
        "public_release_enabled": False,
        "coverage_note": "Successor dataset only; the 2022 overlap is excluded. Later unpublished years are not covered.",
    }
    await writer.write(json.dumps(audit, sort_keys=True).encode(), source_url=METADATA_URL,
                       kind="archive_json", metadata={"endpoint": "complete_refresh_receipt"})
    return writer.artifacts


def organize(artifacts, *, updater, source="co_ins_sivigila_current", **options):
    from src.data.processors.co import ColombiaFetchResult

    updater.normalize_source(source)
    receipts = [item for item in artifacts
                if item.metadata_.get("endpoint") == "complete_refresh_receipt"]
    if len(receipts) != 1:
        raise ValueError("Exactly one complete CO successor refresh receipt required")
    audit = json.loads(read_artifact(receipts[0]))
    if audit.get("source") != source or audit.get("independent_database_acceptance") is not True:
        raise ValueError("Incomplete CO successor refresh receipt")
    # The bounded controller committed the observations through ordinary store
    # stages. This publication check must not write the same rows a second time.
    return ColombiaFetchResult([], None, updater.output_csv,
                               [f"[CO current] {audit['status']}: {audit['source_rows']} stored source rows"],
                               audit)
