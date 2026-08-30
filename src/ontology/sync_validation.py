"""Pure conservation checks shared by ontology synchronization tooling."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def expected_legacy_totals(
    database_snapshot: Mapping[str, Any],
    semantic_repairs: Iterable[Mapping[str, Any]],
) -> tuple[int, int]:
    """Return expected observation/case totals after declared removals."""

    repairs = tuple(semantic_repairs)
    removed_observations = sum(
        int(item.get("would_delete_facts") or 0) for item in repairs
    )
    removed_cases = sum(
        int(item.get("removed_legacy_value") or 0) for item in repairs
    )
    return (
        int(database_snapshot["legacy_observations"]) - removed_observations,
        int(database_snapshot["legacy_cases"]) - removed_cases,
    )


def assert_conserved_totals(
    *,
    label: str,
    expected: tuple[int | float, ...],
    actual: tuple[int | float, ...],
    dimensions: str,
) -> None:
    """Raise a consistent error when a migration changes protected totals."""

    conserved = len(actual) == len(expected) and all(
        (
            left == right
            if isinstance(left, int) and isinstance(right, int)
            else math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-9)
        )
        for left, right in zip(actual, expected)
    )
    if not conserved:
        raise RuntimeError(
            f"Post-migration {label} conservation failed: "
            f"expected {dimensions}={expected}, actual={actual}"
        )
