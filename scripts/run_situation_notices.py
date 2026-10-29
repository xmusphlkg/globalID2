#!/usr/bin/env python3
"""Run the alert-based Situation pipeline end to end.

collect official reports -> store -> fetch full text -> peer-reviewed alert
extraction -> outbreaks -> static export under
``astro-site/src/data/situation/international``.

``--skip-ai`` stores, fetches and exports without calling any model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.global_sources.collection import DEFAULT_SINCE_DAYS  # noqa: E402
from src.services.situation_notices.export import DEFAULT_OUTPUT_DIR  # noqa: E402
from src.services.situation_notices.runner import run_situation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the alert-based Situation pipeline")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--don-limit", type=int, default=200)
    parser.add_argument("--document-limit", type=int, default=40, help="Report bodies downloaded per run")
    parser.add_argument("--ai-limit", type=int, default=30, help="Reports read by the models per run")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--skip-ai", action="store_true", help="Store and export without model calls")
    parser.add_argument("--no-fetch", action="store_true", help="Reuse stored reports; no feed requests")
    parser.add_argument(
        "--require-cross-model",
        action="store_true",
        help="Refuse to run unless two distinct model routes are available",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        run_situation(
            since_days=args.since_days,
            don_limit=args.don_limit,
            document_limit=args.document_limit,
            ai_limit=args.ai_limit,
            output_dir=args.output_dir,
            skip_ai=args.skip_ai,
            require_cross_model=args.require_cross_model,
            fetch=not args.no_fetch,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if summary["export"]["alert_count"] == 0 and summary["export"]["monitoring_products"] == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
