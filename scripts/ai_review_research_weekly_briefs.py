#!/usr/bin/env python3
"""Run the bounded weekly-brief AI quality review (disabled by default)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config  # noqa: E402
from src.literature.weekly_ai_review import (  # noqa: E402
    WEEKLY_AI_REVIEW_REGISTRY_PATH,
    WEEKLY_BRIEF_DIR,
    review_weekly_brief_files,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--weekly-dir", type=Path, default=WEEKLY_BRIEF_DIR)
    parser.add_argument("--registry", type=Path, default=WEEKLY_AI_REVIEW_REGISTRY_PATH)
    parser.add_argument("--apply", action="store_true", help="Persist the content-bound result")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    cfg = get_config().literature
    if not cfg.weekly_ai_review_enabled:
        return {"ok": True, "status": "disabled", "reason": "weekly_ai_review_disabled"}
    result = await review_weekly_brief_files(
        weekly_dir=args.weekly_dir,
        registry_path=args.registry,
        weeks=args.week,
        limit=args.limit or cfg.weekly_ai_review_batch_size,
        timeout_seconds=cfg.weekly_ai_review_timeout_seconds,
        max_attempts=cfg.weekly_ai_review_max_attempts,
        apply=args.apply,
    )
    return {"ok": result["counts"]["failed"] == 0, "status": "applied" if args.apply else "dry_run", **result}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
