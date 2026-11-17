#!/usr/bin/env python3
"""Backfill archived Sri Lanka WER Table 5 observations into a review file.

The script captures the public Wayback CDX manifest and replay originals. It
does not write the application database: review the JSON output and use the
normal source-series ingestion path after source rights/publication approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import requests

from src.data.crawlers.lk_wer import (
    WAYBACK_CDX_URL, discover_wayback_reports, parse_report_pdf,
    SriLankaWERContractError,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2007)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--max-reports", type=int, default=104)
    parser.add_argument("--output", type=Path, default=Path("data/current/lk/lk_wer_history_review.json"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/lk/wer_wayback"))
    args = parser.parse_args()
    if not 1 <= args.max_reports <= 500:
        parser.error("--max-reports must be between 1 and 500")
    query = (WAYBACK_CDX_URL + "?url=www.epid.gov.lk/web/images/pdf/wer/*"
             "&output=json&filter=statuscode:200&collapse=urlkey&limit=1000")
    response = requests.get(query, timeout=60)
    response.raise_for_status()
    reports = discover_wayback_reports(response.content, start_year=args.start_year, end_year=args.end_year)
    selected = reports[:args.max_reports]
    rows, parsed, failures = [], [], []
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    for report in selected:
        try:
            source = requests.get(report["pdf_url"], timeout=60)
            source.raise_for_status()
            payload = source.content
            digest = hashlib.sha256(payload).hexdigest()
            raw_path = args.raw_dir / f"{report['archive_timestamp']}-{digest[:16]}.pdf"
            raw_path.write_bytes(payload)
            report = {**report, "sha256": digest, "raw_path": raw_path.as_posix()}
            parsed_rows, detail = parse_report_pdf(payload, report)
            rows.extend(parsed_rows)
            parsed.append({**report, "detail": detail, "rows": len(parsed_rows)})
        except (requests.RequestException, SriLankaWERContractError, OSError) as exc:
            failures.append({"url": report["pdf_url"], "original_url": report.get("original_url"),
                             "error": str(exc)})
    result = {"scope": "lk_epid_wer_weekly", "source_id": "SRC_LK_EPID_WER",
              "provenance": "Wayback CDX replay of official epid.gov.lk English WER originals",
              "query": query, "requested_start_year": args.start_year, "requested_end_year": args.end_year,
              "discovered_reports": len(reports), "selected_reports": len(selected),
              "parsed_reports": parsed, "failed_reports": failures, "rows": rows,
              "public_release_enabled": False, "db_imported": False,
              "review_required": True, "source_terms": "archive_replay_terms_unverified"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "parsed_reports": len(parsed),
                      "failed_reports": len(failures), "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
