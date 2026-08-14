#!/usr/bin/env python3
"""Repair placeholder Mapping Registry categories using reviewed source identity."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import get_database  # noqa: E402
from src.services.disease_mapping_registry_service import (  # noqa: E402
    disease_mapping_registry_service,
)


async def run(*, apply: bool, publish: bool) -> dict:
    async with get_database() as db:
        if apply:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": "globalid:disease_mapping_release:v1"},
            )
        result = (
            await disease_mapping_registry_service.reconcile_placeholder_source_identities(
                db
            )
        )
        result.update(
            await disease_mapping_registry_service.reclassify_legacy_ai_provider_failures(
                db
            )
        )
        result["mode"] = "applied" if apply else "rehearsed_rollback"
        if apply and publish and result["reconciled_count"]:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            release = await disease_mapping_registry_service.create_release(
                db,
                release_code=f"DMR-RECONCILE-{timestamp}",
                created_by="source_identity_reconciliation",
                description=(
                    "Publish reviewed source-identity inheritance for legacy "
                    "source-current placeholder categories."
                ),
            )
            release.metadata_ = {
                **(release.metadata_ or {}),
                "publication_trigger": "source_current_placeholder_reconciliation",
                "reconciled_count": result["reconciled_count"],
            }
            await disease_mapping_registry_service.activate_release(db, release.id)
            result["release"] = {
                "id": release.id,
                "release_code": release.release_code,
                "status": release.status,
                "checksum": release.checksum,
            }
        elif publish:
            result["release"] = None

        if apply:
            await db.commit()
        else:
            await db.rollback()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--rehearse",
        action="store_true",
        help="Build every inherited assertion and validate it, then roll back",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Persist the safe source-identity inheritance",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Create and activate one immutable mapping release (requires --apply)",
    )
    args = parser.parse_args()
    if args.publish and not args.apply:
        parser.error("--publish requires --apply")
    result = asyncio.run(run(apply=args.apply, publish=args.publish))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
