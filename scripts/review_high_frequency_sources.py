#!/usr/bin/env python3
"""Review exact high-frequency source identities and preserve unrelated mappings."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import text
from src.core.database import get_db, dispose_database
from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES
from src.services.disease_mapping_config import load_reviewed_source_category_mappings
from scripts.review_unpublished_surveillance import (
    LOCK_KEY, preflight, prepare_review, apply_review, create_scoped_release,
    manifest_hashes, verify_manifest_hashes,
)


async def run(*, apply=False):
    sources = {item.source_id for item in HIGH_FREQUENCY_SOURCES.values()} | {"SRC_AT_AGES_RADAR"}
    # Legacy country manifests use definition_version='*'. For a source that
    # now has an exact frozen definition, review only the exact rows; the
    # wildcard remains as historical fallback and must not claim the same
    # category twice in one release plan.
    mappings = [item for item in load_reviewed_source_category_mappings()
                if item.source_id in sources and item.definition_version != "*"]
    frozen = manifest_hashes([ROOT / item.source_path for item in mappings])
    async with get_db() as db:
        await preflight(db)
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY})
        plans, assertions, unmatched, conflicts = await prepare_review(db, mappings)
        if not plans:
            raise ValueError("No captured source categories available for review")
        chosen, changes = await apply_review(db, plans, assertions,
                                            supersede=True, frozen_manifests=frozen)
        release = await create_scoped_release(db, chosen, {category.id for category, _ in plans})
        verify_manifest_hashes(frozen)
        result = {"mode": "applied" if apply else "rehearsed_rollback",
                  "reviewed_at": datetime.now(timezone.utc).isoformat(),
                  "configured_mappings": len(mappings), "matched_categories": len(plans),
                  "unmatched_count": len(unmatched), "unmatched_sample": unmatched[:10],
                  "reviewed_conflicts_superseded": len(conflicts),
                  "changed_assertions": len(changes),
                  "release_id": release.id, "release_code": release.release_code,
                  "release_checksum": release.checksum, "release_metadata": release.metadata_,
                  "manifest_sha256": frozen}
        if apply:
            await db.commit()
        else:
            await db.rollback()
    return result


async def main(args):
    try:
        receipt = await run(apply=args.apply)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    finally:
        await dispose_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
