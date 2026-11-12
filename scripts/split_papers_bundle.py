#!/usr/bin/env python3
"""Split Astro dist into Pages main + papers staging for R2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.papers_bundle import (
    DEFAULT_MAIN_SITE_ORIGIN,
    DEFAULT_PAGES_FILE_BUDGET,
    DEFAULT_PAGES_FILE_WARN,
    DEFAULT_PAPERS_ORIGIN,
    assert_pages_budget,
    split_papers_bundle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=ROOT / "astro-site" / "dist",
        help="Astro dist directory to slim in place",
    )
    parser.add_argument(
        "--papers-dir",
        type=Path,
        default=ROOT / "exports" / "papers-site",
        help="Staging directory for the papers R2 bundle",
    )
    parser.add_argument("--main-origin", default=DEFAULT_MAIN_SITE_ORIGIN)
    parser.add_argument("--papers-origin", default=DEFAULT_PAPERS_ORIGIN)
    parser.add_argument("--pages-file-budget", type=int, default=DEFAULT_PAGES_FILE_BUDGET)
    parser.add_argument("--pages-file-warn", type=int, default=DEFAULT_PAGES_FILE_WARN)
    parser.add_argument(
        "--skip-budget",
        action="store_true",
        help="Split without enforcing the Pages file-count budget",
    )
    parser.add_argument(
        "--research-on-main",
        action="store_true",
        help="Keep research URLs on the main origin instead of redirecting them to the papers host",
    )
    args = parser.parse_args(argv)

    result = split_papers_bundle(
        args.dist_dir,
        papers_dir=args.papers_dir,
        main_origin=args.main_origin,
        papers_origin=args.papers_origin,
        research_on_main=args.research_on_main,
    )
    budget = None
    if not args.skip_budget:
        budget = assert_pages_budget(
            args.dist_dir,
            budget=args.pages_file_budget,
            warn_at=args.pages_file_warn,
        )
    summary = {
        "main_file_count": result.main_file_count,
        "papers_file_count": result.papers_file_count,
        "moved_paths": len(result.moved_paths),
        "papers_dir": str(result.papers_dir),
        "manifest_path": str(result.manifest_path),
        "pages_budget": None
        if budget is None
        else {
            "file_count": budget.file_count,
            "budget": budget.budget,
            "warn_at": budget.warn_at,
            "ok": budget.ok,
            "warn": budget.warn,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if budget is not None and budget.warn:
        print(
            f"warning: Pages file count {budget.file_count} is at or above warn threshold {budget.warn_at}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
