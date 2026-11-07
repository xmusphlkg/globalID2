"""Shared constructors for deterministic Research Radar health checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


def _check(
    code: str,
    status: str,
    observed: Mapping[str, Any],
    threshold: Mapping[str, Any] | None = None,
    *,
    next_action_code: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "next_action_code": next_action_code or (
            "none" if status == "pass" else f"inspect_{code}"
        ),
        "observed": dict(observed),
        "threshold": dict(threshold or {}),
    }


def _blocker_categories(blockers: Sequence[str]) -> dict[str, int]:
    categories: Counter[str] = Counter()
    patterns = (
        ("bilingual", "bilingual_gate"),
        ("classification", "classification"),
        ("private fields", "private_data"),
        ("integrity", "integrity"),
        ("duplicate", "duplicate"),
        ("peer-reviewed", "peer_review"),
        ("preprint", "peer_review"),
        ("signal", "surveillance_evidence"),
        ("weekly brief", "weekly_brief"),
        ("editorial", "editorial_gate"),
        ("indexable", "editorial_gate"),
        ("research domain", "classification"),
    )
    for blocker in blockers:
        lowered = blocker.lower()
        category = next((label for needle, label in patterns if needle in lowered), "other")
        categories[category] += 1
    return dict(sorted(categories.items()))
