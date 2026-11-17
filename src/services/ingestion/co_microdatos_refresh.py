"""Task entry point for microdata aggregation; never replaces public curves.

Ordinary runs reuse the local official archive. Force refreshes its catalogue
and originals before deriving the latest closed-year weekly record aggregates.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.data.crawlers.co_microdatos import DEFAULT_SCOPE, LANDING_URL
from src.data.processors.co import ColombiaFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.co_current_refresh import ROOT, _run


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=2025, end_year=2025,
                  force=False, **options):
    if source != DEFAULT_SCOPE:
        raise ValueError("Unsupported microdata source")
    if force:
        await _run([ROOT / "scripts/imports/collect_co_microdatos.py", "--workers", "4"],
                   writer.directory / "microdata-capture.log")
    output = writer.directory.resolve() / "microdata-aggregates"
    await _run([ROOT / "scripts/imports/co_microdatos_weekly.py", "--start-year", start_year,
                "--end-year", end_year, "--output", output, "--workers", "4"],
               writer.directory / "microdata-aggregation.log")
    audit = json.loads((output / "audit.json").read_bytes())
    if audit["aggregated_workbooks"] == 0:
        raise ValueError("No microdata workbooks passed individual-record validation")
    await writer.write((output / "audit.json").read_bytes(), source_url=LANDING_URL,
                       kind="archive_json", metadata={"endpoint": "microdata_aggregation_audit"})
    await writer.write((output / "weekly_notification_records.csv").read_bytes(), source_url=LANDING_URL,
                       kind="historical_csv", metadata={"endpoint": "microdata_weekly_records"})
    return writer.artifacts


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    import csv
    import io
    from datetime import date

    updater.normalize_source(source)
    audits = [a for a in artifacts if a.metadata_.get("endpoint") == "microdata_aggregation_audit"]
    tables = [a for a in artifacts if a.metadata_.get("endpoint") == "microdata_weekly_records"]
    if len(audits) != 1 or len(tables) != 1:
        raise ValueError("Exactly one microdata audit and aggregate CSV required")
    audit = json.loads(read_artifact(audits[0]))
    payload = read_artifact(tables[0])
    import hashlib
    if audit["source"] != source or hashlib.sha256(payload).hexdigest() != audit["source_csv_sha256"]:
        raise ValueError("Microdata aggregate differs from its audited output")
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    if len(rows) != audit["weekly_strata"] or sum(int(r["Cases"]) for r in rows) != audit["aggregated_records"]:
        raise ValueError("Microdata weekly aggregates do not reconcile")
    return ColombiaFetchResult(rows, max((date.fromisoformat(r["Date"]) for r in rows), default=None),
                               Path(audit["source_csv"]),
                               [f"[CO microdata] {len(rows)} private weekly classification strata"], audit)
