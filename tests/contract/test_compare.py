"""Comparator self-tests: a one-row difference must fail the comparison."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.contract.compare import (
    Diff,
    assert_observations_match,
    assert_records_match,
    diff_observations,
    diff_records,
    observation_key,
    record_fingerprint,
)


def _obs(time, *, value=5.0, missing=None):
    return {
        "time": time,
        "series_code": "US:flu:confirmed",
        "geography_key": "national",
        "dimension_key": "all",
        "value": value,
        "suppressed": False,
        "quality_status": "raw",
        "metadata": {"missing_value_status": missing} if missing else {},
    }


A = _obs("2026-10-20T00:00:00+00:00")
B = _obs("2026-10-21T00:00:00+00:00")


def test_identical_rows_produce_empty_diff():
    assert diff_observations([A, B], [B, A]).is_empty


def test_one_missing_row_is_reported():
    diff = diff_observations([A], [A, B])
    assert not diff.is_empty
    assert diff.only_expected == [observation_key(B)]


def test_one_changed_value_is_reported():
    diff = diff_observations([A], [_obs("2026-10-20T00:00:00+00:00", value=6.0)])
    assert diff.changed[0][0] == observation_key(A)
    assert diff.changed[0][1][-1] is None and diff.changed[0][2][-1] is None
    assert diff.changed[0][1][4] == 5.0 and diff.changed[0][2][4] == 6.0


def test_missing_value_status_is_part_of_the_fingerprint():
    explicit = _obs("2026-10-20T00:00:00+00:00", value=None, missing="source_missing")
    suppressed = {
        **explicit,
        "suppressed": True,
        "metadata": {"missing_value_status": "suppressed"},
    }
    diff = diff_observations([explicit], [suppressed])
    assert diff.changed[0][0] == observation_key(explicit)


def test_assertion_raises_on_a_one_row_difference():
    with pytest.raises(AssertionError) as exc:
        assert_observations_match([A], [A, B])
    assert "observation" in str(exc.value)
    assert "only in expected" not in str(exc.value).lower() or "in expected" in str(
        exc.value
    )


def test_aware_times_fold_to_utc():
    local = datetime(2026, 10, 20, 1, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    assert observation_key(_obs("2026-10-20T00:00:00+00:00")) == observation_key(
        _obs(local)
    )


def test_record_comparison_includes_cases_and_deaths():
    record = {
        "time": date(2026, 10, 20),
        "disease_id": 7,
        "country_id": 1,
        "cases": 10,
        "deaths": 1,
    }
    other = {**record, "deaths": 2}
    assert diff_records([record], [record]).is_empty
    diff = diff_records([record], [other])
    assert diff.changed[0][1] == record_fingerprint(record)
    with pytest.raises(AssertionError):
        assert_records_match([record], [other])


def test_diff_is_falsey_only_when_empty():
    assert not Diff()
    assert Diff(only_actual=[1])


@pytest.mark.parametrize("side", ["actual", "expected", "both"])
@pytest.mark.parametrize("different_value", [False, True])
@pytest.mark.parametrize("kind", ["observation", "record"])
def test_duplicate_natural_keys_cannot_hide_rows(side, different_value, kind):
    if kind == "observation":
        row = A
        other = {**row, "value": 900} if different_value else dict(row)
        compare, assert_match = diff_observations, assert_observations_match
    else:
        row = {
            "time": "2026-10-20",
            "disease_id": 7,
            "country_id": 1,
            "cases": 10,
            "deaths": 1,
        }
        other = {**row, "deaths": 900} if different_value else dict(row)
        compare, assert_match = diff_records, assert_records_match
    # The final row matches: the old dictionary comprehension silently lost
    # the first, potentially different row.
    actual = [other, row] if side in {"actual", "both"} else [row]
    expected = [other, row] if side in {"expected", "both"} else [row]
    diff = compare(iter(actual), iter(expected))
    assert diff and not diff.is_empty
    assert bool(diff.duplicate_actual) == (side in {"actual", "both"})
    assert bool(diff.duplicate_expected) == (side in {"expected", "both"})
    duplicates = diff.duplicate_actual or diff.duplicate_expected
    assert len(duplicates[0][1]) == 2
    assert "2 rows" in diff.render(kind)
    if different_value:
        assert "900" in diff.render(kind)
    with pytest.raises(AssertionError, match=f"duplicate {kind} natural key"):
        assert_match(actual, expected)


def test_duplicate_different_values_are_order_independent_and_reported():
    changed = {**A, "value": 900}
    first = diff_observations([changed, A], [A])
    reverse = diff_observations([A, changed], [A])
    assert first == reverse
    assert first.changed
    assert "900" in first.render()


def test_duplicate_key_normalizes_equivalent_times():
    equivalent = {**A, "time": "2026-10-20T01:00:00+01:00"}
    assert diff_observations([A, equivalent], [A]).duplicate_actual


def test_nullable_natural_keys_can_be_reported_together():
    null_dimension = {**A, "dimension_key": None}
    assert len(diff_observations([A, null_dimension], []).only_actual) == 2
