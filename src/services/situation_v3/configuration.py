"""Strict Situation Room v3.2 configuration validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = "situation_room.v3.2"
METHOD_VERSION = "situation_room_v3.2"
PUBLICATION_Q_CANDIDATES = {0.0025, 0.005, 0.01, 0.015, 0.025}


def _unknown_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"Unknown {path} configuration keys: {', '.join(unknown)}")


def calibration_definition_hash(config: dict[str, Any]) -> str:
    """Hash detector and source definitions whose change invalidates calibration."""

    policy = config.get("publication", {}).get("auto_verification", {})
    groups = policy.get("groups", {}) if isinstance(policy, dict) else {}
    definition = {
        "v3": {
            key: value
            for key, value in config.get("v3", {}).items()
            if key != "maximum_analysis_workers"
        },
        "minimum_observations": config.get("thresholds", {}).get(
            "minimum_observations", {}
        ),
        "quality": {
            "minimum_window_completeness": config.get("quality", {}).get(
                "minimum_window_completeness"
            ),
            "source_evidence_urls": config.get("quality", {}).get(
                "source_evidence_urls", {}
            ),
        },
        "data_latency": config.get("data_latency", {}),
        "source_policy": {
            key: {
                field: value.get(field, [])
                for field in (
                    "allowed_source_systems",
                    "canary_source_systems",
                    "authoritative_source_domains",
                )
            }
            for key, value in groups.items()
            if isinstance(value, dict)
        },
        "official_evidence": config.get("publication", {}).get(
            "official_evidence", {}
        ),
    }
    return hashlib.sha256(
        json.dumps(
            definition,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_v32_config(config: dict[str, Any]) -> dict[str, Any]:
    """Reject legacy or ambiguous detector/publication configuration."""

    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Situation v3 requires schema_version={SCHEMA_VERSION}; "
            f"got {config.get('schema_version')!r}"
        )
    v3 = config.get("v3")
    if not isinstance(v3, dict) or v3.get("method_version") != METHOD_VERSION:
        raise ValueError(f"Situation v3 requires method_version={METHOD_VERSION}")
    _unknown_keys(
        v3,
        {
            "method_version",
            "model",
            "alert_q",
            "strong_q",
            "fdr_method",
            "fdr_family",
            "maximum_analysis_workers",
            "detectors",
            "effect_gates",
            "detector_tiers",
        },
        "v3",
    )
    if v3.get("model") != "multi_horizon_gamma_poisson_v1":
        raise ValueError("v3.model must be multi_horizon_gamma_poisson_v1")
    detectors = v3.get("detectors")
    if not isinstance(detectors, dict) or set(detectors) != {"multi_horizon"}:
        raise ValueError("v3.detectors must contain only multi_horizon")
    multi = detectors["multi_horizon"]
    if not isinstance(multi, dict):
        raise ValueError("v3.detectors.multi_horizon must be an object")
    _unknown_keys(
        multi,
        {
            "enabled",
            "version",
            "horizons",
            "production_draws",
            "calibration_draws",
        },
        "v3.detectors.multi_horizon",
    )
    if multi.get("enabled") is not True:
        raise ValueError("v3.detectors.multi_horizon.enabled must be true")
    horizons = multi.get("horizons")
    if not isinstance(horizons, dict) or set(horizons) != {"weekly", "monthly"}:
        raise ValueError("multi_horizon.horizons must define weekly and monthly only")
    expected_horizons = {"weekly": [1, 2, 4], "monthly": [1, 2]}
    for cadence, expected in expected_horizons.items():
        if list(horizons.get(cadence) or []) != expected:
            raise ValueError(f"{cadence} horizons must be {expected}")
    for key in ("production_draws", "calibration_draws"):
        draws = int(multi.get(key, 0))
        if draws < 512 or draws > 65_536:
            raise ValueError(f"{key} must be between 512 and 65536")

    publication = config.get("publication")
    policy = publication.get("auto_verification") if isinstance(publication, dict) else None
    if not isinstance(policy, dict):
        raise ValueError("publication.auto_verification must be configured")
    _unknown_keys(
        policy,
        {
            "enabled",
            "mode",
            "kill_switch",
            "policy_version",
            "calibration_hash",
            "calibration_definition_hash",
            "minimum_complete_null_families",
            "maximum_false_publication_upper_95",
            "minimum_sensitivity",
            "maximum_median_delay_periods",
            "minimum_completeness",
            "groups",
            "official_corroboration",
        },
        "publication.auto_verification",
    )
    mode = str(policy.get("mode") or "")
    if mode not in {"off", "shadow", "canary", "live"}:
        raise ValueError("auto_verification.mode is invalid")
    groups = policy.get("groups")
    expected_groups = {"weekly.common_count", "monthly.common_count"}
    if not isinstance(groups, dict) or set(groups) != expected_groups:
        raise ValueError(
            "auto_verification.groups must define weekly.common_count and "
            "monthly.common_count"
        )
    allowed_group_keys = {
        "enabled",
        "maximum_q",
        "complete_null_family_trials",
        "false_publication_upper_95",
        "sensitivity",
        "median_detection_delay_periods",
        "allowed_source_systems",
        "canary_source_systems",
        "authoritative_source_domains",
    }
    for group_key, group in groups.items():
        if not isinstance(group, dict):
            raise ValueError(f"{group_key} must be an object")
        _unknown_keys(group, allowed_group_keys, f"auto_verification.groups.{group_key}")
        maximum_q = float(group.get("maximum_q", 0.0))
        if maximum_q != 0.0 and maximum_q not in PUBLICATION_Q_CANDIDATES:
            raise ValueError(f"{group_key}.maximum_q is not pre-registered")
        if bool(group.get("enabled", False)) and maximum_q == 0.0:
            raise ValueError(f"{group_key} cannot be enabled with maximum_q=0")
        if bool(group.get("enabled", False)):
            for list_key in (
                "allowed_source_systems",
                "authoritative_source_domains",
            ):
                if not list(group.get(list_key) or []):
                    raise ValueError(
                        f"{group_key}.{list_key} must be non-empty when enabled"
                    )
            configured_source_urls = (
                config.get("quality", {}).get("source_evidence_urls", {})
            )
            missing_source_urls = sorted(
                set(group.get("allowed_source_systems") or [])
                - set(configured_source_urls)
            )
            if missing_source_urls:
                raise ValueError(
                    f"{group_key} allowlisted sources lack canonical evidence URLs: "
                    + ", ".join(missing_source_urls)
                )
    if mode != "off" and not bool(policy.get("enabled", False)):
        raise ValueError("non-off automation mode requires enabled=true")
    if bool(policy.get("enabled", False)) and not policy.get("calibration_hash"):
        raise ValueError("enabled automation requires calibration_hash")
    if bool(policy.get("enabled", False)) and not policy.get(
        "calibration_definition_hash"
    ):
        raise ValueError("enabled automation requires calibration_definition_hash")
    official_evidence = publication.get("official_evidence")
    if not isinstance(official_evidence, dict):
        raise ValueError("publication.official_evidence must be configured")
    if int(official_evidence.get("match_window_periods", -1)) != 2:
        raise ValueError("official_evidence.match_window_periods must be 2")
    if not list(official_evidence.get("authoritative_domains") or []):
        raise ValueError("official_evidence.authoritative_domains must be non-empty")
    return config


__all__ = [
    "METHOD_VERSION",
    "PUBLICATION_Q_CANDIDATES",
    "SCHEMA_VERSION",
    "calibration_definition_hash",
    "validate_v32_config",
]
