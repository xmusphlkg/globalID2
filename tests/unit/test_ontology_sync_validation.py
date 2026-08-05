from __future__ import annotations

import pytest

from src.ontology.sync_validation import (
    assert_conserved_totals,
    expected_legacy_totals,
)


def test_expected_legacy_totals_apply_only_declared_removals() -> None:
    snapshot = {"legacy_observations": 100, "legacy_cases": 1_250}
    repairs = [
        {"would_delete_facts": 2, "removed_legacy_value": 30},
        {"would_delete_facts": 0, "removed_legacy_value": 7},
    ]

    assert expected_legacy_totals(snapshot, repairs) == (98, 1_213)


def test_expected_legacy_totals_accept_missing_optional_counts() -> None:
    assert expected_legacy_totals(
        {"legacy_observations": "4", "legacy_cases": "9"}, [{}]
    ) == (4, 9)


def test_conservation_check_reports_protected_dimensions() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"series conservation failed.*observations/suppressed/value",
    ):
        assert_conserved_totals(
            label="series",
            expected=(10, 2, 7.0),
            actual=(9, 2, 7.0),
            dimensions="observations/suppressed/value",
        )


def test_conservation_check_accepts_equal_totals() -> None:
    assert_conserved_totals(
        label="legacy",
        expected=(5, 12),
        actual=(5, 12),
        dimensions="observations/cases",
    )
