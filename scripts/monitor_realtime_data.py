#!/usr/bin/env python3
"""Read-only publication health samples; exit 0/1/2 for ok/warn/error."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with redirect_stderr(io.StringIO()):
    import httpx
    from loguru import logger
    from sqlalchemy import text
    from src.core.config import get_config
    from src.core.database import dispose_database, get_engine, get_session_maker
    from src.services.data_release.channel_control import get_publication_controls
    from src.services.data_release.research_monitor import probe_public_research, reserve_sampling_cursor
    from src.services.data_release.realtime_monitor import (
        CHANNELS, DATABASE_CHANNELS, LEVEL, MonitorThresholds, assemble_report, collect_database_snapshot,
        probe_public_manifest, report_text, validate_public_url,
    )
logger.remove()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--channel", choices=DATABASE_CHANNELS, action="append", help="Repeat to select channels; default legacy pair plus Literature when enabled")
    result.add_argument("--origin", help="Public main-site origin; default configured main_site_origin")
    result.add_argument("--surveillance-manifest-url", default=os.environ.get("REALTIME_SURVEILLANCE_MANIFEST_URL"))
    result.add_argument("--situation-manifest-url", default=os.environ.get("REALTIME_SITUATION_MANIFEST_URL"))
    result.add_argument("--literature-manifest-url", default=os.environ.get("REALTIME_LITERATURE_MANIFEST_URL"))
    result.add_argument("--format", choices=("json", "text"), default="json")
    result.add_argument("--pretty", action="store_true")
    result.add_argument("--interval-seconds", type=float, default=30)
    result.add_argument("--samples", type=int, default=1, help="Number of checks; default one")
    result.add_argument("--watch", action="store_true", help="Continue until interrupted or duration expires")
    result.add_argument("--duration-seconds", type=float, help="Bound observation duration (also enables watch)")
    result.add_argument("--archive-dir", type=Path, help="Append UTC-dated NDJSON and atomic latest.json locally")
    result.add_argument("--research-sampling-state-file", type=Path,
        help="Persist Literature round-robin position; default <archive-dir>/research-sampling.json")
    result.add_argument("--warning-exit-zero", action="store_true", help="Report warnings normally but return 0 (critical errors still fail)")
    result.add_argument("--timeout-seconds", type=float, default=10, help="Hard deadline for each HTTP channel probe and DB snapshot")
    result.add_argument("--object-limit-bytes", type=int, default=2 * 1024 * 1024)
    for key, default in vars(MonitorThresholds()).items():
        result.add_argument("--" + key.replace("_", "-"), type=float, default=default)
    return result


async def database_snapshot(*, now, channels):
    # No application get_db: it commits on success. Enforce a consistent,
    # read-only PostgreSQL transaction and roll it back on every exit path.
    get_engine().echo = False
    async with get_session_maker()() as db:
        try:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            return await collect_database_snapshot(db, now=now, channels=channels)
        finally:
            await db.rollback()


async def check_once(args, thresholds, *, client, database_reader=database_snapshot):
    now = datetime.now(timezone.utc)
    config = get_config().data_release
    defaults = (*CHANNELS, "literature") if getattr(config, "literature_json_enabled", False) else CHANNELS
    channels = tuple(dict.fromkeys(args.channel or defaults))
    origin = (args.origin or config.main_site_origin).rstrip("/")
    urls = {"surveillance": args.surveillance_manifest_url or origin + "/site-data/current.json",
        "situation": args.situation_manifest_url or origin + "/site-data/situation/current.json",
        "literature": args.literature_manifest_url or origin + "/site-data/research-publication/current.json"}
    for channel in channels:
        validate_public_url(urls[channel])
    async def read_db():
        try:
            return await asyncio.wait_for(database_reader(now=now, channels=channels), args.timeout_seconds), None
        except asyncio.TimeoutError:
            return None, "database_timeout"
        except Exception:
            return None, "database_unavailable"
    async def read_public(channel):
        options = {"timeout_seconds": args.timeout_seconds, "object_limit_bytes": args.object_limit_bytes}
        if channel == "literature":
            state_path = args.research_sampling_state_file or (
                args.archive_dir / "research-sampling.json" if args.archive_dir else None)
            if state_path:
                try:
                    cursor = await asyncio.to_thread(reserve_sampling_cursor, state_path,
                        target=urls[channel] + "\n" + config.r2_papers_public_base_url.rstrip("/"))
                except (OSError, ValueError, TypeError):
                    return {"available": False, "error_code": "research_sampling_state_unavailable", "attempts": 0}
            else:
                cursor = getattr(args, "_research_sample_cursor", 0)
                args._research_sample_cursor = cursor + 1
            return await probe_public_research(client, urls[channel], papers_origin=config.r2_papers_public_base_url,
                                              sample_cursor=cursor, **options)
        return await probe_public_manifest(client, urls[channel], **options)
    results = await asyncio.gather(read_db(), *(read_public(channel) for channel in channels))
    (database, error), *public = results
    return assemble_report(database, dict(zip(channels, public)), now=now,
        thresholds=thresholds, database_error=error, controls=get_publication_controls())


def archive_report(directory: Path, report: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with (directory / (report["checked_at"][:10] + ".ndjson")).open("a", encoding="utf-8") as stream:
        stream.write(body)
    # Unique temporary filename also supports overlapping operator/timer checks.
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".latest-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(body)
        os.replace(temporary, directory / "latest.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


async def observe(args, thresholds) -> int:
    worst, samples, started = 0, 0, time.monotonic()
    async with httpx.AsyncClient(timeout=args.timeout_seconds, follow_redirects=False,
                                headers={"User-Agent": "GlobalID-Realtime-Monitor/1"}) as client:
        while True:
            report = await check_once(args, thresholds, client=client)
            if args.archive_dir:
                try:
                    archive_report(args.archive_dir, report)
                except OSError:
                    report["archive_error_code"] = "archive_write_failed"
                    report["status"] = "critical"
            print(report_text(report) if args.format == "text" else json.dumps(report,
                ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None), flush=True)
            worst = max(worst, LEVEL[report["status"]])
            samples += 1
            if not (args.watch or args.duration_seconds is not None) and samples >= args.samples:
                return worst
            remaining = args.duration_seconds - (time.monotonic() - started) if args.duration_seconds is not None else None
            if remaining is not None and remaining <= 0:
                return worst
            await asyncio.sleep(min(args.interval_seconds, remaining) if remaining is not None else args.interval_seconds)
            if args.duration_seconds is not None and time.monotonic() - started >= args.duration_seconds:
                return worst


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if (not all(math.isfinite(value) for value in (args.interval_seconds, args.timeout_seconds))
                or args.interval_seconds <= 0 or args.samples < 1 or args.timeout_seconds <= 0
                or args.object_limit_bytes < 1 or (args.duration_seconds is not None
                    and (not math.isfinite(args.duration_seconds) or args.duration_seconds <= 0))):
            raise ValueError("invalid_monitor_options")
        thresholds = MonitorThresholds(**{key: getattr(args, key) for key in vars(MonitorThresholds())})
        async def run():
            try:
                return await observe(args, thresholds)
            finally:
                await dispose_database()
        status = asyncio.run(run())
        return 0 if args.warning_exit_zero and status == LEVEL["warning"] else status
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"schema_version": 1, "service": "realtime-data", "status": "critical",
            "error_code": "monitor_check_failed"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
