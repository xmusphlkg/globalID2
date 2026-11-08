#!/usr/bin/env python3
"""Move report archive pages out of Astro dist into the reports R2 bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.papers_bundle import DEFAULT_MAIN_SITE_ORIGIN, DEFAULT_PAGES_FILE_BUDGET, DEFAULT_PAGES_FILE_WARN
from src.services.data_release.reports_bundle import DEFAULT_REPORTS_ORIGIN, assert_reports_pages_budget, split_reports_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "astro-site" / "dist")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "exports" / "reports-site")
    parser.add_argument("--main-origin", default=DEFAULT_MAIN_SITE_ORIGIN)
    parser.add_argument("--reports-origin", default=DEFAULT_REPORTS_ORIGIN)
    parser.add_argument("--pages-file-budget", type=int, default=DEFAULT_PAGES_FILE_BUDGET)
    parser.add_argument("--pages-file-warn", type=int, default=DEFAULT_PAGES_FILE_WARN)
    parser.add_argument("--skip-budget", action="store_true")
    args = parser.parse_args(argv)
    result = split_reports_bundle(args.dist_dir, reports_dir=args.reports_dir, main_origin=args.main_origin, reports_origin=args.reports_origin)
    budget = None if args.skip_budget else assert_reports_pages_budget(args.dist_dir, budget=args.pages_file_budget, warn_at=args.pages_file_warn)
    print(json.dumps({
        "main_file_count": result.main_file_count,
        "reports_file_count": result.reports_file_count,
        "moved_paths": len(result.moved_paths),
        "reports_dir": str(result.reports_dir),
        "manifest_path": str(result.manifest_path),
        "pages_budget": None if budget is None else {"file_count": budget.file_count, "budget": budget.budget, "warn_at": budget.warn_at, "ok": budget.ok, "warn": budget.warn},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
