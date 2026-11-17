#!/usr/bin/env python3
"""Capture multiple BES years concurrently, or replay verified frozen originals.

--capture-only saves complete original files without claiming numerical data.
--apply requires successful offline organization before database registration.
Proxy settings are read server-side by the INS-specific transport only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.imports.import_surveillance_archive import (
    import_archive,
    load_archive_manifest,
)
from scripts.imports.kh_camewarn_history import FileSnapshotWriter
from src.data.crawlers.co_bes import (
    DEFAULT_SCOPE,
    HISTORY_START_YEAR,
    BESContractError,
    NotYetVerifiedContract,
)
from src.data.crawlers.co_ins_transport import (
    INSTransportError,
    ins_transport_readiness,
)
from src.data.processors.co_bes import ColombiaINSBESUpdater
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.countries.co_bes import collect, organize

DEFAULT_STATE = ROOT / "data/current/co/bes-history-state.json"


class BESSnapshotWriter(FileSnapshotWriter):
    """Keep the standard local snapshot manifest and return the saved artifact."""

    def __init__(self, directory):
        directory = Path(directory).resolve()
        if not directory.is_relative_to((ROOT / "data/raw/co").resolve()):
            raise ValueError("BES snapshot directory must be inside data/raw/co")
        super().__init__(directory)

    async def write(self, payload, **options):
        await super().write(payload, **options)
        if (options.get("metadata") or {}).get("endpoint") == "pdf":
            count = sum(a.metadata_.get("endpoint") == "pdf" for a in self.artifacts)
            if count == 1 or count % 5 == 0:
                print(json.dumps({"stage": "capture", "reports_saved": count}), flush=True)
        return self.artifacts[-1]


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def capture_summary(artifacts):
    plans = [a for a in artifacts if a.metadata_.get("endpoint") == "collection_plan"]
    if len(plans) != 1:
        raise BESContractError("Exactly one complete BES capture plan is required")
    plan = json.loads(read_artifact(plans[0]))
    captured = [a for a in artifacts if a.metadata_.get("endpoint") == "pdf"]
    return {
        "raw_reports": len(captured),
        "planned_reports": len(plan["reports"]),
        "failed_downloads": len(plan["failed_downloads"]),
        "numeric_contract_status": "not_yet_verified",
        "report_periods_verified": False,
    }


async def run(args):
    start, end = int(args.start_year), int(args.end_year)
    if not HISTORY_START_YEAR <= start <= end <= datetime.now(timezone.utc).year:
        raise ValueError("BES years must be within 2025 and the current year")
    state = {
        "source": DEFAULT_SCOPE, "start_year": start, "end_year": end,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "capturing", "database_imported": False,
        "coverage_verified": False, "public_release_enabled": False,
        "transport": ins_transport_readiness(),
    }
    save_state(args.state_path, state)
    try:
        if args.replay_manifest:
            manifest = args.replay_manifest.resolve()
            artifacts = await asyncio.to_thread(load_archive_manifest, manifest, "CO")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            writer = BESSnapshotWriter(args.raw_directory or ROOT / "data/raw/co/bes-history" / stamp)
            manifest = writer.directory / "manifest.json"
            state["raw_manifest"] = str(manifest.relative_to(ROOT))
            save_state(args.state_path, state)
            await collect(writer, source=DEFAULT_SCOPE, start_year=start, end_year=end,
                          download_workers=args.download_workers, parse_workers=args.parse_workers,
                          force=args.force, cache_directory=args.cache_directory)
            # Re-read every original from disk and verify the frozen bytes before replay.
            artifacts = await asyncio.to_thread(load_archive_manifest, manifest, "CO")
        state["raw_manifest"] = str(manifest.relative_to(ROOT))
        state.update(capture_summary(artifacts))
        if state["failed_downloads"] or state["raw_reports"] != state["planned_reports"] or not state["raw_reports"]:
            raise BESContractError("BES capture incomplete; inspect the frozen collection plan and retry using the cache")
        state["status"] = "captured_awaiting_source_contract"
        save_state(args.state_path, state)
        if args.capture_only:
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            save_state(args.state_path, state)
            return state, 0
        fetched = await asyncio.to_thread(
            organize, artifacts, updater=ColombiaINSBESUpdater(), source=DEFAULT_SCOPE,
            start_year=start, end_year=end, parse_workers=args.parse_workers)
        if not fetched.rows:
            raise BESContractError("BES organization produced no verified observations")
        state.update(status="organized_not_imported", organized_rows=len(fetched.rows),
                     numeric_contract_status="verified_by_organizer",
                     report_periods_verified=not fetched.source_audit.get("failed_reports"),
                     source_unreadable_cells=fetched.source_audit.get("source_unreadable_cells", 0))
        if args.apply:
            receipt = await import_archive(
                "CO", manifest, apply=True, source=DEFAULT_SCOPE, start_year=start,
                parse_workers=args.parse_workers,
                receipt_path=ROOT / "data/current/co/bes_database_import_receipt.json")
            state.update(status="imported_pending_reconciliation", database_imported=True,
                         imported_records=receipt["records"], task_uuid=receipt["task_uuid"])
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_state(args.state_path, state)
        return state, 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary persists safe failure state.
        if isinstance(exc, NotYetVerifiedContract):
            state["status"] = "captured_awaiting_source_contract"
        elif isinstance(exc, INSTransportError) and not state["transport"]["configured"]:
            state["status"] = "awaiting_colombian_egress"
        else:
            state["status"] = "failed_import" if args.apply and state.get("organized_rows") else "failed_capture_or_organization"
        # Never persist third-party exception strings: proxy errors may contain credentials.
        state["error_type"] = type(exc).__name__
        if isinstance(exc, BESContractError):
            state["source_contract_error"] = str(exc)
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_state(args.state_path, state)
        return state, 2


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--download-workers", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--parse-workers", type=int, choices=range(1, 33), default=16)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--capture-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--raw-directory", type=Path)
    parser.add_argument("--replay-manifest", type=Path)
    parser.add_argument("--cache-directory", type=Path, default=ROOT / "data/cache/co/ins_bes_reports")
    parser.add_argument("--force", action="store_true", help="Re-fetch cached originals from INS")
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE)
    return parser


def main():
    parser = argument_parser()
    args = parser.parse_args()
    if args.replay_manifest and args.raw_directory:
        parser.error("--replay-manifest and --raw-directory cannot be combined")
    state, code = asyncio.run(run(args))
    print(json.dumps(state, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
