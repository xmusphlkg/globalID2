#!/usr/bin/env python3
"""Fully validate a local GlobalID sharded download package.

This command is intentionally read-only and has no network or deployment
behaviour.  It can optionally compare v2 totals with the legacy v1 manifest
during the dual-publish migration window.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.sharded_data_package import (  # noqa: E402
    validate_sharded_data_package,
)


def _legacy_record_totals(path: Path) -> dict[str, int]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read legacy manifest {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"Legacy manifest root must be an object: {path}")

    totals: dict[str, int] = {}
    for kind in ("countries", "diseases"):
        entries = document.get(kind)
        if not isinstance(entries, list):
            raise RuntimeError(f"Legacy manifest field {kind!r} must be a list")
        total = 0
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise RuntimeError(
                    f"Legacy manifest {kind}[{index}] must be an object"
                )
            record_count = entry.get("record_count")
            if (
                isinstance(record_count, bool)
                or not isinstance(record_count, int)
                or record_count < 0
            ):
                raise RuntimeError(
                    f"Legacy manifest {kind}[{index}].record_count is invalid"
                )
            total += record_count
        totals[kind] = total
    return totals


def verify_package(
    package_dir: Path,
    *,
    legacy_manifest: Path | None = None,
    expected_records: int | None = None,
) -> dict[str, Any]:
    """Validate every package object and return a machine-readable summary."""

    result = validate_sharded_data_package(package_dir)
    manifest = result.manifest
    summary: dict[str, Any] = {
        "status": "ok",
        "package": str(package_dir.resolve()),
        "manifest_version": manifest.get("manifest_version"),
        "package_mode": manifest.get("package_mode"),
        "record_count": result.record_count,
        "shard_count": result.shard_count,
        "compressed_bytes": result.compressed_bytes,
        "uncompressed_bytes": result.uncompressed_bytes,
    }

    if expected_records is not None and result.record_count != expected_records:
        raise RuntimeError(
            "v2 record total does not match --expected-records: "
            f"v2={result.record_count}, expected={expected_records}"
        )

    indexes = manifest.get("indexes")
    if manifest.get("package_mode") == "canonical_facts":
        if not isinstance(indexes, dict):
            raise RuntimeError("Canonical facts manifest is missing indexes")
        index_totals: dict[str, int] = {}
        for kind in ("countries", "diseases"):
            descriptors = indexes.get(kind)
            if not isinstance(descriptors, list):
                raise RuntimeError(f"Canonical index {kind!r} must be a list")
            index_totals[kind] = sum(
                int(item["record_count"]) for item in descriptors
            )
            if index_totals[kind] != result.record_count:
                raise RuntimeError(
                    f"{kind} index total does not match canonical facts: "
                    f"index={index_totals[kind]}, facts={result.record_count}"
                )
        summary["index_record_totals"] = index_totals

    if legacy_manifest is not None:
        legacy_totals = _legacy_record_totals(legacy_manifest)
        for kind, total in legacy_totals.items():
            if total != result.record_count:
                raise RuntimeError(
                    f"Legacy {kind} total does not match v2 canonical facts: "
                    f"legacy={total}, v2={result.record_count}"
                )
        summary["legacy_manifest"] = str(legacy_manifest.resolve())
        summary["legacy_record_totals"] = legacy_totals

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate all hashes, sizes, paths, records, and indexes in a local v2 package"
    )
    parser.add_argument("package_dir", type=Path)
    parser.add_argument(
        "--legacy-manifest",
        type=Path,
        help="Optional v1 manifest whose country/disease totals must equal v2",
    )
    parser.add_argument(
        "--expected-records",
        type=int,
        help="Optional exact canonical fact count",
    )
    args = parser.parse_args()
    summary = verify_package(
        args.package_dir,
        legacy_manifest=args.legacy_manifest,
        expected_records=args.expected_records,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
