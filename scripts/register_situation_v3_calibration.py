#!/usr/bin/env python3
"""Validate and optionally register an immutable Situation v3.2 calibration artifact."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_room import load_config  # noqa: E402
from src.services.situation_v3.configuration import (  # noqa: E402
    calibration_definition_hash,
)
from src.services.situation_v3.persistence import record_calibration_run_v3  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the registry row; default is a read-only validation.",
    )
    return parser.parse_args()


def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _group_is_supported(group: dict[str, Any]) -> bool:
    maximum_q = group.get("maximum_q")
    threshold = (group.get("thresholds") or {}).get(str(maximum_q))
    real = group.get("locked_real_event_metrics") or {}
    weak = group.get("weak_signal_improvement_vs_champion_pp") or {}
    stresses = group.get("null_stress_strata") or {}
    if not isinstance(threshold, dict):
        return False
    interval = threshold.get("false_publication_rate_ci_95") or {}
    return bool(
        group.get("status") == "supported"
        and maximum_q in {0.0025, 0.005, 0.01, 0.015, 0.025}
        and int(threshold.get("complete_null_family_trials", 0)) >= 384
        and float(interval.get("upper", 1.0)) <= 0.025
        and float(threshold.get("sustained_2x_sensitivity", -1.0)) >= 0.80
        and float(threshold.get("median_detection_delay_periods", 999.0)) <= 1.0
        and weak
        and all(float(value) >= 15.0 for value in weak.values())
        and float(real.get("event_detection_rate", -1.0)) >= 0.80
        and float(real.get("event_detection_rate", -1.0))
        >= float(real.get("champion_event_detection_rate", 1.0)) - 0.05
        and set(stresses)
        == {
            "zero_inflation",
            "correlated_series",
            "missing_periods",
            "revisions",
            "structural_break",
            "delayed_data",
        }
        and all(int(row.get("family_trials", 0)) > 0 for row in stresses.values())
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    raw = args.artifact.read_bytes()
    artifact = json.loads(raw)
    if not isinstance(artifact, dict):
        raise ValueError("calibration artifact must be a JSON object")
    artifact_hash = hashlib.sha256(raw).hexdigest()
    current_config = load_config()
    current_config_hash = _config_hash(current_config)
    current_definition_hash = calibration_definition_hash(current_config)
    artifact_config_hash = str(artifact.get("config_hash") or "")
    artifact_definition_hash = str(
        artifact.get("calibration_definition_hash") or ""
    )
    groups = artifact.get("calibration_groups") or {}
    required_groups = {"weekly.common_count", "monthly.common_count"}
    missing_groups = sorted(required_groups.difference(groups))
    supported = (
        not missing_groups
        and artifact_config_hash == current_config_hash
        and artifact_definition_hash == current_definition_hash
        and all(_group_is_supported(groups[key]) for key in required_groups)
    )
    status = "supported" if supported else "not_supported"
    calibrated_at = datetime.now(timezone.utc).replace(microsecond=0)
    calibration_id = "calibration-v3:" + artifact_hash[:24]
    reasons = []
    if artifact_config_hash != current_config_hash:
        reasons.append("config_hash_mismatch")
    if artifact_definition_hash != current_definition_hash:
        reasons.append("calibration_definition_hash_mismatch")
    if missing_groups:
        reasons.append("missing_required_groups")
    reasons.extend(
        f"{key}:not_supported"
        for key in sorted(required_groups.intersection(groups))
        if groups[key].get("status") != "supported"
    )
    if args.apply:
        await record_calibration_run_v3(
            calibration_id=calibration_id,
            method_version="situation_room_v3.2",
            config_hash=artifact_config_hash,
            artifact_hash=artifact_hash,
            artifact_uri=str(args.artifact.resolve()),
            status=status,
            calibrated_at=calibrated_at,
            window_start=(
                date.fromisoformat(artifact["window_start"])
                if artifact.get("window_start")
                else None
            ),
            window_end=(
                date.fromisoformat(artifact["window_end"])
                if artifact.get("window_end")
                else None
            ),
            summary={
                "passed": bool(artifact.get("passed")),
                "failure_reasons": reasons,
                "simulation_protocol_hash": artifact.get(
                    "simulation_protocol_hash"
                ),
            },
            group_results=groups,
        )
    return {
        "mode": "applied" if args.apply else "dry_run",
        "calibration_id": calibration_id,
        "artifact_hash": artifact_hash,
        "config_hash": artifact_config_hash,
        "current_config_hash": current_config_hash,
        "calibration_definition_hash": artifact_definition_hash,
        "current_calibration_definition_hash": current_definition_hash,
        "status": status,
        "failure_reasons": reasons,
    }


def main() -> int:
    args = parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
