#!/usr/bin/env python3
"""Audit stored Springer/Elsevier coverage without API calls or database writes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_IMPORT_LOG_SINK = io.StringIO()
with redirect_stderr(_IMPORT_LOG_SINK):
    from loguru import logger  # noqa: E402
    from sqlalchemy import create_engine, text  # noqa: E402

    from src.core.config import get_config  # noqa: E402
    from src.literature.publisher_coverage import (  # noqa: E402
        DEFAULT_RECENT_DAYS,
        DEFAULT_TOP_JOURNALS,
        build_publisher_coverage_report,
        collect_publisher_coverage_rows,
    )

logger.remove()


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=_iso_date,
        default=datetime.now(timezone.utc).date(),
        help="Inclusive audit date in YYYY-MM-DD (default: current UTC date)",
    )
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS)
    parser.add_argument("--top-journals", type=int, default=DEFAULT_TOP_JOURNALS)
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    return parser


def _print_json(value: dict[str, Any], *, pretty: bool) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
    )


def _source_configuration(settings: Any) -> dict[str, dict[str, bool]]:
    return {
        "springer_nature": {
            "enabled": bool(settings.springer_nature_enabled),
            "credential_configured": bool(settings.springer_nature_api_key),
        },
        "elsevier": {
            "enabled": bool(settings.elsevier_enabled),
            "credential_configured": bool(settings.elsevier_api_key),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.recent_days < 1 or args.top_journals < 1:
        _print_json(
            {
                "schema_version": 1,
                "service": "research-radar-publisher-coverage",
                "status": "check_error",
                "error_code": "positive_limits_required",
            },
            pretty=args.pretty,
        )
        return 2

    engine = None
    try:
        settings = get_config()
        engine = create_engine(settings.database.url_sync, pool_pre_ping=True)
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                rows = collect_publisher_coverage_rows(
                    connection,
                    as_of=args.as_of,
                    recent_days=args.recent_days,
                )
            finally:
                transaction.rollback()
        report = build_publisher_coverage_report(
            rows,
            as_of=args.as_of,
            recent_days=args.recent_days,
            top_journals=args.top_journals,
            source_configuration=_source_configuration(settings.literature),
        )
    except Exception:
        # Do not expose connection URLs, identifiers, query text, or provider
        # configuration through this machine-readable operational boundary.
        _print_json(
            {
                "schema_version": 1,
                "service": "research-radar-publisher-coverage",
                "status": "check_error",
                "error_code": "publisher_coverage_check_failed",
            },
            pretty=args.pretty,
        )
        return 3
    finally:
        if engine is not None:
            engine.dispose()

    _print_json(report, pretty=args.pretty)
    return 0 if report["source_strategy"]["publisher_apis_required_now"] is False else 1


if __name__ == "__main__":
    raise SystemExit(main())
