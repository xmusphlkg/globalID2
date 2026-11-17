#!/usr/bin/env python3
"""Audit or explicitly install the reviewed effective-mapping view definition.

Default mode is READ ONLY. Applying requires hashes for the current live view,
the exact desired SQL and the source files inspected by the operator. This
command never changes raw observations, mappings, releases or publication gates.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import get_config
from src.services.disease_mapping_view import EFFECTIVE_MAPPING_VIEW_NAME, effective_mapping_view_sql


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def source_digest() -> str:
    """Bind review to the installer, fixed SQL and runtime initialization code."""
    names = (
        "scripts/install_effective_mapping_view.py",
        "src/services/disease_mapping_view.py",
        "src/services/disease_mapping_registry_service.py",
    )
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    return digest(json.dumps(hashes, sort_keys=True))


def validate_expected_hashes(report: dict, args) -> None:
    for field, expected in (
        ("existing_view_sha256", args.expected_existing_view_sha256),
        ("definition_sha256", args.expected_definition_sha256),
        ("source_sha256", args.expected_source_sha256),
    ):
        if not expected or expected != report[field]:
            raise ValueError(f"Missing or changed review hash: {field}")


async def run(args) -> dict:
    statement = effective_mapping_view_sql()
    sources_before = source_digest()
    engine = create_async_engine(get_config().database.url)
    try:
        async with engine.connect() as db:
            if args.apply:
                # Serialize with mapping-release changes. The DDL remains in
                # this transaction and requires a bounded lock acquisition.
                await db.execute(text("SET LOCAL lock_timeout = '10s'"))
                await db.execute(text("SET LOCAL statement_timeout = '30s'"))
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                    "key": "globalid:disease_mapping_release:v1"
                })
            else:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            existing = (await db.execute(text("""
                SELECT pg_get_viewdef(view.oid, true)
                FROM pg_class view
                JOIN pg_namespace namespace ON namespace.oid=view.relnamespace
                WHERE namespace.nspname=current_schema()
                  AND view.relname=:name AND view.relkind='v'
            """), {"name": EFFECTIVE_MAPPING_VIEW_NAME})).scalar_one_or_none()
            report = {
                "view": EFFECTIVE_MAPPING_VIEW_NAME,
                "mode": "apply" if args.apply else "read_only_audit",
                "existing_view_exists": existing is not None,
                "existing_view_sha256": digest(existing or ""),
                "definition_sha256": digest(statement),
                "source_sha256": sources_before,
                "dynamic_holding_metadata_precedence_present": bool(existing and "dynamic_holding" in existing),
                "installed": False,
            }
            if args.apply:
                validate_expected_hashes(report, args)
                if source_digest() != sources_before:
                    raise ValueError("Source files changed during view preflight")
                await db.execute(text(statement))
                installed = (await db.execute(text("SELECT pg_get_viewdef(CAST(:name AS regclass), true)"), {
                    "name": EFFECTIVE_MAPPING_VIEW_NAME
                })).scalar_one()
                if "dynamic_holding" not in installed or source_digest() != sources_before:
                    raise ValueError("View installation did not match the reviewed definition/source")
                report["installed_view_sha256"] = digest(installed)
                await db.commit()
                report["installed"] = True
            else:
                await db.rollback()
            return report
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-existing-view-sha256")
    parser.add_argument("--expected-definition-sha256")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.apply and not all((args.expected_existing_view_sha256, args.expected_definition_sha256, args.expected_source_sha256)):
        parser.error("--apply requires all three --expected-*-sha256 review hashes")
    report = asyncio.run(run(args))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
