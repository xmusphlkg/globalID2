#!/usr/bin/env python3
"""Evaluate a narrow true-weekly contract against all frozen BES originals.

Writes a review dataset and gaps; no database or website publication changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.collect_co_microdatos import write_json
from src.data.crawlers.co_bes import BESReport
from src.data.crawlers.co_bes_weekly import PARSER_VERSION, parse_weekly_notifications
from src.data.crawlers.co_microdatos import sha256_file


def run(manifest, output):
    document = json.loads(manifest.read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    observations, audits, gaps = [], [], []
    for artifact in document["artifacts"]:
        metadata = artifact.get("metadata_", {})
        if metadata.get("endpoint") != "pdf":
            continue
        path = (ROOT / artifact["relative_path"]).resolve()
        if not path.is_relative_to((ROOT / "data/raw/co").resolve()) or sha256_file(path) != artifact["sha256"]:
            raise ValueError("BES frozen original identity mismatch")
        report = BESReport(**metadata["report"])
        try:
            rows, audit = parse_weekly_notifications(path.read_bytes(), report)
            observations.extend(rows)
            audits.append(audit)
        except ValueError as exc:
            gaps.append({"source_url": report.pdf_url, "reason": str(exc), "sha256": artifact["sha256"]})
    observations.sort(key=lambda row: (row["source_epi_year"], row["source_epi_week"], row["event_code"]))
    keys = [(r["source_epi_year"], r["source_epi_week"], r["event_code"]) for r in observations]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate national weekly observation")
    path = output / "weekly_notifications.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({key for row in observations for key in row}) if observations else ["value"])
        writer.writeheader()
        writer.writerows(observations)
    result = {"parser_version": PARSER_VERSION, "manifest_sha256": sha256_file(manifest),
              "parsed_reports": len(audits), "failed_reports": len(gaps), "points": len(observations),
              "event_codes": sorted({r["event_code"] for r in observations}),
              "csv_sha256": sha256_file(path), "gaps": gaps, "reports": audits,
              "database_imported": False, "public_release_enabled": False}
    write_json(output / "audit.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"reports", "gaps"}}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/co/bes-weekly-notifications-preview")
    args = parser.parse_args()
    run(args.manifest, args.output)
