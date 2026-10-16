#!/usr/bin/env python3
"""Discover reviewed source categories and publish one atomic v3 mapping release."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json

from sqlalchemy import text

from src.core import get_database
from src.services.disease_mapping_registry_service import disease_mapping_registry_service


async def run(*, apply: bool) -> dict:
    async with get_database() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": "globalid:disease_mapping_release:v1"},
        )
        imported = await disease_mapping_registry_service.bootstrap_all_sources(db)
        reviewed = imported["reviewed_mapping_config"]
        if reviewed["conflicts"]:
            await db.rollback()
            raise RuntimeError(
                f"Reviewed mapping conflicts require manual resolution: "
                f"{len(reviewed['conflicts'])}; first={reviewed['conflicts'][:3]}"
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        release = await disease_mapping_registry_service.create_release(
            db,
            release_code=f"DMR-REVIEWED-{timestamp}",
            created_by="reviewed_source_mapping_release",
            description="Publish reviewed source-category mappings after six-country historical backfill.",
        )
        await disease_mapping_registry_service.activate_release(db, release.id)
        result = {
            "mode": "applied" if apply else "rehearsed_rollback",
            "release": {
                "id": release.id,
                "release_code": release.release_code,
                "status": release.status,
                "checksum": release.checksum,
                "assertion_count": release.metadata_.get("assertion_count"),
            },
            "discovery": {
                key: imported[key]
                for key in (
                    "ontology_categories_created",
                    "observed_categories_created",
                    "approved_assertions_imported",
                    "series_assertions_inherited",
                    "series_inheritance_conflicts",
                    "retired_synthetic_categories",
                )
            },
            "reviewed": {
                key: reviewed[key]
                for key in ("configured", "matched_categories", "assertions_imported", "already_present")
            },
            "unmatched_manifest_count": len(reviewed["unmatched"]),
            "unmatched_manifest_sample": reviewed["unmatched"][:10],
        }
        if apply:
            await db.commit()
        else:
            await db.rollback()
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rehearse", action="store_true", help="Validate in one transaction, then roll back")
    mode.add_argument("--apply", action="store_true", help="Activate and commit the reviewed release")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
