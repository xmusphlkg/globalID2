#!/usr/bin/env python3
"""Build a local Korea KDCA historical monthly snapshot.

This command only writes a CSV and raw portal responses under ``data/``. It
does not create crawl tasks, update PostgreSQL, or replace the current feed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.crawlers.kr import (
    KoreaKDCAOpenAPICrawler,
    DEFAULT_DPORTAL_STATS_AJAX_URL,
    normalize_kdca_download_rows,
)


def _offline_rows(raw_dir: Path, start_year: int, end_year: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(raw_dir.glob("page_*.json")):
        try:
            year = int(path.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if not start_year <= year <= end_year:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "payload" in payload:
            payload = payload["payload"]
        rows.extend(
            normalize_kdca_download_rows(
                KoreaKDCAOpenAPICrawler._extract_json_rows(payload),
                fallback_year=year,
                source_name="Korea KDCA EID Portal Download",
                source_url=DEFAULT_DPORTAL_STATS_AJAX_URL,
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2001)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/current/kr/korea_national_monthly_historical.csv"),
    )
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/kr/historical"))
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use previously saved portal/page_YYYY.json files without network access",
    )
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("--start-year must be no greater than --end-year")

    crawler = KoreaKDCAOpenAPICrawler(save_raw=True, raw_dir=args.raw_dir)
    try:
        if args.offline:
            rows = _offline_rows(args.raw_dir / "portal", args.start_year, args.end_year)
        else:
            target_months = {
                (year, month)
                for year in range(args.start_year, args.end_year + 1)
                for month in range(1, 13)
            }
            rows = crawler._fetch_portal_monthly_rows(
                years=range(args.start_year, args.end_year + 1),
                target_months=target_months,
            )
        rows = [dict(values) for values in {tuple(sorted(row.items())) for row in rows}]
        rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
        if not rows:
            raise RuntimeError("KDCA history produced no normalized monthly rows")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        crawler._write_national_csv(args.output, rows)
        print(
            f"wrote {args.output} rows={len(rows)} "
            f"years={args.start_year}-{args.end_year}"
        )
    finally:
        crawler.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
