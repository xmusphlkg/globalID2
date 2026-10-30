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
    assert "only in expected" not in str(exc.value).lower() or "in expected" in str(exc.value)


def test_aware_times_fold_to_utc():
    local = datetime(2026, 10, 20, 1, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    assert observation_key(_obs("2026-10-20T00:00:00+00:00")) == observation_key(_obs(local))


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
