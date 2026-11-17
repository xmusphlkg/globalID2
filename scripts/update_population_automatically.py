"""Scheduled official population discovery, isolated by country source.

Each child discovers the current official release, archives it, validates all
local annual totals and (with --apply) imports semantic changes. One unavailable
source cannot prevent the others from refreshing. No static CSV is an input.
Only changed denominators request the existing dirty-scope publication job.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "data/processed/official_population"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def combine_successful_snapshots(output: Path, statuses: dict, *, now: str) -> None:
    """Attest an effective snapshot without throwing away a failed source's data."""
    previous = read_json(output / "latest.json", {})
    rows = {}
    if previous.get("snapshot"):
        with (Path(previous["snapshot"]) / "population.csv").open() as stream:
            rows.update(((row["country_code"], row["year"]), row) for row in csv.DictReader(stream))
    for state in statuses.values():
        snapshot = state.get("last_success_snapshot")
        if not snapshot:
            continue
        with (Path(snapshot) / "population.csv").open() as stream:
            rows.update(((row["country_code"], row["year"]), row) for row in csv.DictReader(stream))
    if not rows:
        return
    buffer = io.StringIO()
    fields = ["country_code", "year", "population", "source", "reference_date", "source_url", "basis"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in sorted(rows.values(), key=lambda row: (row["country_code"], int(row["year"]))):
        writer.writerow(row)
    body = buffer.getvalue().encode()
    snapshot = output / "scheduled" / now.replace(":", "").replace("+", "_")
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "population.csv").write_bytes(body)
    report = {"applied": True, "checked_at": now, "snapshot": str(snapshot), "rows": len(rows), "locations": len({key[0] for key in rows}), "input_sha256": hashlib.sha256(body).hexdigest(), "source_statuses": statuses, "policy": "exact geography, same calendar year, no interpolation or carry-forward"}
    write_json(snapshot / "report.json", report)
    write_json(output / "latest.json", report)


async def queue_population_release():
    from src.core.database import dispose_database
    from src.services.data_release_service import data_release_service
    try:
        return await data_release_service.trigger_job(
            "site-release", manual=True, trigger="official-population-scheduled",
            data_only=True, data_line="surveillance", suppress_subscription_dispatch=True,
        )
    finally:
        await dispose_database()


def refresh_sources(*, output: Path, sources: list[str], apply: bool, queue_release: bool, runner=subprocess.run, now: str | None = None) -> dict:
    now = now or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    status_path = output / ("scheduler-status.json" if apply else "scheduler-plan.json")
    previous = read_json(status_path, {})
    states = previous.get("sources", {})
    failures, changed = [], set()
    pending = bool(previous.get("release_pending"))
    run_dir = output / "checks" / now.replace(":", "").replace("+", "_")
    run_dir.mkdir(parents=True, exist_ok=True)
    for code in sources:
        source_output = output / "sources" / code.lower()
        command = [sys.executable, str(ROOT / "scripts/refresh_official_population.py"), "--sources", code, "--output", str(source_output)]
        if apply:
            command.append("--apply")
        state = {**states.get(code, {}), "last_attempt_at": now}
        try:
            result = runner(command, cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False)
            (run_dir / f"{code.lower()}.log").write_text((result.stdout or "") + (result.stderr or ""))
            if result.returncode:
                raise RuntimeError(f"Official {code} refresh failed (exit {result.returncode}); see {run_dir / (code.lower() + '.log')}")
            report = read_json(source_output / ("latest.json" if apply else "latest_plan.json"), None)
            if not report or report.get("applied") != apply:
                raise ValueError("Source refresh has no validated completion receipt")
            source_changes = report.get("changes") or {}
            changed.update(source_changes.get("changed_country_codes") or [])
            pending = pending or bool(changed)
            state.update({"status": "healthy", "last_success_at": now, "last_success_snapshot": report["snapshot"], "rows": report["rows"], "coverage": report["coverage"], "official_sources": report["sources"], "changed_rows": source_changes.get("changed_rows", 0), "consecutive_failures": 0, "last_error": None})
            print(f"{code}: checked official publications, {source_changes.get('changed_rows', 0)} population rows changed", flush=True)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            failures.append(code)
            state.update({"status": "failed", "last_error": str(error), "consecutive_failures": int(state.get("consecutive_failures", 0)) + 1})
            print(f"{code}: {error}", file=sys.stderr, flush=True)
        states[code] = state
        # Update health after every country, including an interrupted batch.
        write_json(status_path, {"checked_at": now, "applied": apply, "sources": states, "release_pending": pending})
    if apply:
        combine_successful_snapshots(output, states, now=now)
    report = {"checked_at": now, "applied": apply, "sources": states, "failed_sources": failures, "changed_country_codes": sorted(changed)}
    # Keep a failed publication retry pending even on a later no-change check.
    report["release_pending"] = pending
    if apply and queue_release and pending:
        try:
            result = asyncio.run(queue_population_release())
            if result.get("status") not in {"queued", "running", "skipped"}:
                raise RuntimeError(f"Population publication was not accepted: {result.get('status')}")
            # A busy job may have frozen its input before these population changes.
            report["release_pending"] = result.get("status") != "queued"
            report["release"] = result
        except Exception as error:
            report["release_error"] = str(error)
            failures.append("publication")
    report["failed_sources"] = failures
    write_json(status_path, report)
    write_json(run_dir / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--queue-release", action="store_true")
    parser.add_argument("--sources", nargs="+", choices=["CN", "AU", "BR", "US", "JP", "SE"], default=["CN", "AU", "BR", "US", "JP", "SE"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.queue_release and not args.apply:
        parser.error("--queue-release requires --apply")
    result = refresh_sources(output=args.output, sources=args.sources, apply=args.apply, queue_release=args.queue_release)
    print(json.dumps({key: result[key] for key in ("applied", "failed_sources", "changed_country_codes", "release_pending")}))
    return 1 if result["failed_sources"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
