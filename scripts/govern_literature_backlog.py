#!/usr/bin/env python3
"""Plan or explicitly apply a bounded Research Radar backlog policy."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.governance import audit_current_backlog, governance_plan  # noqa: E402
from src.services.literature_automation_service import literature_automation_service  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and govern the actionable Research Radar review backlog"
    )
    parser.add_argument(
        "--article-min-score",
        type=float,
        help="Project a calibrated article publication threshold; current configuration is the default",
    )
    parser.add_argument("--max-projected-backlog", type=int, default=500)
    parser.add_argument("--max-changes", type=int, default=5000)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the exact reviewed plan; default behavior is read-only",
    )
    parser.add_argument(
        "--confirm-plan-sha256",
        help="Required with --apply and must match the plan hash printed by a fresh dry run",
    )
    parser.add_argument("--no-export", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> tuple[int, dict]:
    if args.max_projected_backlog < 0 or args.max_changes < 0:
        return 2, {"applied": False, "error": "governance guards must be non-negative"}
    overrides = (
        {"autopilot_article_min_score": args.article_min_score}
        if args.article_min_score is not None
        else None
    )
    audit = await audit_current_backlog()
    preview = await literature_automation_service.reconcile(
        dry_run=True,
        export=False,
        policy_overrides=overrides,
        diagnostics=True,
    )
    plan = governance_plan(preview, max_projected_backlog=args.max_projected_backlog)
    result: dict = {"read_only": not args.apply, "audit": audit, "plan": plan, "applied": False}
    if not args.apply:
        return 0, result
    if not args.confirm_plan_sha256:
        result["error"] = "--confirm-plan-sha256 is required with --apply"
        return 2, result
    if args.confirm_plan_sha256 != plan["plan_sha256"]:
        result["error"] = "confirmed plan hash does not match the fresh dry-run plan"
        return 2, result
    if not plan["autopilot_enabled"]:
        result["error"] = "autopilot must be enabled before a governance plan can be applied"
        return 2, result
    if not plan["within_backlog_guard"]:
        result["error"] = "projected actionable backlog exceeds the configured guard"
        return 2, result
    if plan["counts"]["changed"] > args.max_changes:
        result["error"] = "projected change count exceeds the configured guard"
        return 2, result
    applied = await literature_automation_service.reconcile(
        dry_run=False,
        export=not args.no_export,
        policy_overrides=overrides,
        diagnostics=True,
    )
    # Article identifiers are useful to internal reconciliation but do not
    # belong in an operator-facing aggregate governance report.
    applied.pop("published_article_ids", None)
    result.update({"read_only": False, "applied": True, "result": applied})
    return 0, result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, result = asyncio.run(_run(args))
    except ValueError as exc:
        code, result = 2, {"applied": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
