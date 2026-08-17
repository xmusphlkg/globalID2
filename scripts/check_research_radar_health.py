#!/usr/bin/env python3
"""Emit a privacy-safe, read-only Research Radar health report as JSON."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr
from dataclasses import asdict
import io
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing the application database layer initializes Loguru.  Bind that
# initialization to a private sink, then remove its sinks so stdout/stderr stay
# a strict machine interface for this command. Operational detail remains in
# the aggregate report rather than side-channel log lines.
_IMPORT_LOG_SINK = io.StringIO()
with redirect_stderr(_IMPORT_LOG_SINK):
    from loguru import logger  # noqa: E402
    from src.core.config import get_config  # noqa: E402
    from src.core.database import get_session_maker  # noqa: E402
    from src.literature.health import (  # noqa: E402
        DEFAULT_RELEASE_PATH,
        HealthThresholds,
        collect_health_snapshot,
        evaluate_health,
        exit_code_for,
    )
    from src.literature.metadata_backfill import DEFAULT_CHECKPOINT_PATH  # noqa: E402

logger.remove()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thresholds",
        type=Path,
        help="JSON object overriding any HealthThresholds field",
    )
    parser.add_argument("--release-path", type=Path, default=DEFAULT_RELEASE_PATH)
    parser.add_argument("--backfill-checkpoint", type=Path, default=DEFAULT_CHECKPOINT_PATH)
    parser.add_argument(
        "--fail-on",
        choices=("warning", "critical"),
        default="warning",
        help="Whether warning/degraded reports should return a non-zero exit code",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    parser.add_argument("--max-sync-age-hours", type=float)
    parser.add_argument("--max-source-lag-hours", type=float)
    parser.add_argument("--max-consecutive-failures", type=int)
    parser.add_argument("--min-classification-current-ratio", type=float)
    parser.add_argument("--min-openalex-coverage", type=float)
    parser.add_argument("--min-unpaywall-coverage", type=float)
    parser.add_argument("--max-release-age-hours", type=float)
    parser.add_argument("--max-digest-age-days", type=float)
    parser.add_argument("--max-latest-failed-task-types", type=int)
    parser.add_argument("--max-exception-backlog", type=int)
    return parser


def _load_thresholds(args: argparse.Namespace) -> HealthThresholds:
    values: dict[str, Any] = {}
    if args.thresholds:
        try:
            loaded = json.loads(args.thresholds.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("threshold_file_missing") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("threshold_file_unreadable_or_invalid_json") from exc
        if not isinstance(loaded, dict):
            raise ValueError("threshold_file_object_required")
        values.update(loaded)
    for key in (
        "max_sync_age_hours",
        "max_source_lag_hours",
        "max_consecutive_failures",
        "min_classification_current_ratio",
        "min_openalex_coverage",
        "min_unpaywall_coverage",
        "max_release_age_hours",
        "max_digest_age_days",
        "max_latest_failed_task_types",
        "max_exception_backlog",
    ):
        value = getattr(args, key)
        if value is not None:
            values[key] = value
    return HealthThresholds.from_mapping({**asdict(HealthThresholds()), **values})


async def _report(args: argparse.Namespace, thresholds: HealthThresholds) -> dict[str, Any]:
    # Bypass get_db's commit-on-success context.  No ORM value is modified and
    # the read transaction is explicitly rolled back after the SELECT snapshot.
    session_factory = get_session_maker()
    async with session_factory() as db:
        try:
            snapshot = await collect_health_snapshot(
                db,
                thresholds=thresholds,
                release_path=args.release_path,
                backfill_checkpoint_path=args.backfill_checkpoint,
                settings=get_config().literature,
            )
        finally:
            await db.rollback()
    return evaluate_health(snapshot, thresholds)


def _print_json(value: dict[str, Any], *, pretty: bool) -> None:
    print(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        thresholds = _load_thresholds(args)
        report = asyncio.run(_report(args, thresholds))
    except Exception:
        # The underlying exception can contain a database URL, task UUID,
        # article identifier, remote response, or filesystem location.
        _print_json({
            "schema_version": 1,
            "service": "research-radar",
            "status": "check_error",
            "error_code": "health_check_failed",
        }, pretty=args.pretty)
        return 3
    _print_json(report, pretty=args.pretty)
    return exit_code_for(report, fail_on=args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
