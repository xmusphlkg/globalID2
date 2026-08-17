#!/usr/bin/env python3
"""Verify that a downloaded Situation artifact matches its gated manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.automation.run_situation_release import build_steps, dist_inventory


SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


class ArtifactVerificationError(RuntimeError):
    """Raised when a manifest or downloaded artifact is not deployable."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ArtifactVerificationError(f"{field}_object_required")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactVerificationError("manifest_not_found") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVerificationError("manifest_unreadable_or_invalid_json") from exc
    return dict(_mapping(value, "manifest"))


def verify_artifact(
    manifest_path: Path,
    dist_dir: Path,
    *,
    expected_run_id: str | None = None,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if manifest.get("status") != "passed" or manifest.get("deployment_ready") is not True:
        raise ArtifactVerificationError("manifest_not_deployment_ready")
    if expected_run_id is not None and manifest.get("run_id") != expected_run_id:
        raise ArtifactVerificationError("manifest_run_id_mismatch")
    source_commit = str(manifest.get("source_commit") or "")
    if expected_source_commit is not None:
        normalized_expected = expected_source_commit.strip().lower()
        if not SHA_RE.fullmatch(normalized_expected):
            raise ArtifactVerificationError("invalid_expected_source_commit")
        if source_commit.lower() != normalized_expected:
            raise ArtifactVerificationError("manifest_source_commit_mismatch")

    expected_step_names = [
        step.name for step in build_steps(python_executable="python")
    ]
    raw_steps = manifest.get("steps")
    if not isinstance(raw_steps, list):
        raise ArtifactVerificationError("manifest_steps_array_required")
    actual_step_names: list[str] = []
    for value in raw_steps:
        step = _mapping(value, "manifest_step")
        if step.get("status") != "passed":
            raise ArtifactVerificationError("manifest_contains_unpassed_step")
        actual_step_names.append(str(step.get("name") or ""))
    if actual_step_names != expected_step_names:
        raise ArtifactVerificationError("manifest_step_sequence_mismatch")

    expected_inventory = _mapping(manifest.get("dist"), "manifest_dist")
    try:
        actual_inventory = dist_inventory(
            dist_dir,
            root_label=str(expected_inventory.get("root") or "dist"),
        )
    except RuntimeError as exc:
        raise ArtifactVerificationError(f"artifact_invalid:{exc}") from exc
    for field in ("file_count", "total_bytes", "tree_sha256", "release_files"):
        if expected_inventory.get(field) != actual_inventory.get(field):
            raise ArtifactVerificationError(f"artifact_{field}_mismatch")
    return {
        "status": "verified",
        "run_id": manifest.get("run_id"),
        "source_commit": source_commit,
        "file_count": actual_inventory["file_count"],
        "total_bytes": actual_inventory["total_bytes"],
        "tree_sha256": actual_inventory["tree_sha256"],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--expected-run-id")
    parser.add_argument("--expected-source-commit")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_artifact(
            args.manifest,
            args.dist,
            expected_run_id=args.expected_run_id,
            expected_source_commit=args.expected_source_commit,
        )
    except ArtifactVerificationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
