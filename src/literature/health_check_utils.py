"""Shared constructors for deterministic Research Radar health checks."""

from __future__ import annotations

from collections.abc import Mapping
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
