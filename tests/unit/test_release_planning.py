"""Publication batches preserve exact acknowledgements and legacy boundaries."""
from types import SimpleNamespace

import pytest

from src.services.data_release.planning import plan_changes


def marker(identifier, scope, *, line="surveillance", precise=True, **change_overrides):
    changes = {"version": 1, "precise": True, "series": [{"series_code": "cases"}], **change_overrides} if precise else None
    return SimpleNamespace(id=identifier, line=line, scope_key=scope, change_set=changes)


def test_precise_national_changes_do_not_expand_into_subdivisions():
    rows = [marker(1, "US"), marker(2, "JP")]
    plan = plan_changes(rows, {"US", "US-AK", "US-CA", "JP", "JP-01"})
    assert plan["country_codes"] == ["JP", "US"]
    assert plan["dirty_ids"] == [1, 2]
    assert plan["continuation_dirty_ids"] == []
    assert [change["change_set"] for change in plan["changes"]] == [row.change_set for row in rows]


def test_legacy_parent_group_larger_than_budget_is_atomic_and_runs_alone():
    family = {"US", *(f"US-{index:02}" for index in range(10))}
    plan = plan_changes([marker(1, "US", precise=False), marker(2, "JP")], family | {"JP"})
    assert plan["country_codes"] == sorted(family)
    assert plan["dirty_ids"] == [1]
    assert plan["oversized_legacy_scopes"] == ["US"]
    assert plan["continuation_dirty_ids"] == [2]
    assert plan["deferred_change_count"] == 1


def test_oversized_legacy_group_waits_whole_if_another_country_was_selected():
    family = {"US", *(f"US-{index:02}" for index in range(10))}
    rows = [marker(1, "JP"), marker(2, "US", precise=False), marker(3, "US")]
    first = plan_changes(rows, family | {"JP"})
    assert first["country_codes"] == ["JP"]
    assert first["dirty_ids"] == [1]
    assert first["continuation_dirty_ids"] == [2, 3]
    second = plan_changes([row for row in rows if row.id in first["continuation_dirty_ids"]], family | {"JP"})
    assert second["country_codes"] == sorted(family)
    assert second["dirty_ids"] == [2, 3]
    assert second["continuation_dirty_ids"] == []


def test_already_covered_child_markers_join_an_oversized_parent_batch():
    family = {"US", *(f"US-{index:02}" for index in range(10))}
    rows = [marker(1, "US", precise=False), marker(2, "US-01"), marker(3, "US-01"), marker(4, "JP")]
    plan = plan_changes(rows, family | {"JP"})
    assert plan["country_codes"] == sorted(family)
    assert plan["dirty_ids"] == [1, 2, 3]
    assert plan["continuation_dirty_ids"] == [4]
    assert len(plan["country_codes"]) == len(set(plan["country_codes"]))


def test_default_eight_country_batches_ack_only_selected_visible_ids():
    codes = {f"C{index:02}" for index in range(18)}
    rows = [marker(index + 1, f"C{index:02}") for index in range(18)]
    # A second change for an already selected scope fits without another country slot.
    rows.append(marker(25, "C00"))
    first = plan_changes(rows, codes)
    assert first["country_budget"] == 8
    assert first["country_codes"] == [f"C{index:02}" for index in range(8)]
    assert first["dirty_ids"] == [*range(1, 9), 25]
    assert first["continuation_dirty_ids"] == list(range(9, 19))
    # A later concurrent commit is outside the frozen continuation and remains unacknowledged.
    concurrent = marker(99, "C00")
    assert concurrent.id not in first["dirty_ids"] + first["continuation_dirty_ids"]
    second_rows = [row for row in rows if row.id in first["continuation_dirty_ids"]]
    second = plan_changes(second_rows, codes)
    third = plan_changes([row for row in second_rows if row.id in second["continuation_dirty_ids"]], codes)
    assert [len(batch["country_codes"]) for batch in [first, second, third]] == [8, 8, 2]
    assert third["continuation_dirty_ids"] == []
    selected = [identifier for batch in [first, second, third] for identifier in batch["dirty_ids"]]
    assert sorted(selected) == sorted(row.id for row in rows)
    assert len(selected) == len(set(selected))


def test_disabled_scopes_remain_pending_without_endless_continuation():
    rows = [marker(1, "DISABLED"), marker(2, "US"), marker(3, "JP"), marker(4, "DISABLED")]
    first = plan_changes(rows, {"US", "JP"}, max_countries=1)
    assert first["dirty_ids"] == [2]
    assert first["deferred_change_count"] == 3
    assert first["continuation_dirty_ids"] == [3]
    second = plan_changes([row for row in rows if row.id in first["continuation_dirty_ids"]], {"US", "JP"}, max_countries=1)
    assert second["dirty_ids"] == [3]
    assert second["continuation_dirty_ids"] == []
    disabled = plan_changes([rows[0], rows[3]], set())
    assert disabled["dirty_ids"] == disabled["country_codes"] == disabled["continuation_dirty_ids"] == []
    assert disabled["deferred_change_count"] == 2


def test_non_surveillance_markers_coalesce_without_consuming_country_budget():
    rows = [marker(1, "US"), marker(2, "JP"), marker(3, "all", line="literature"),
            marker(4, "all", line="literature"), marker(5, "all", line="situation"),
            marker(6, "flu", line="knowledge")]
    plan = plan_changes(rows, {"US", "JP"}, max_countries=1)
    assert plan["country_codes"] == ["US"]
    assert plan["dirty_ids"] == [1, 3, 4, 5, 6]
    assert plan["continuation_dirty_ids"] == [2]
    assert [change["id"] for change in plan["changes"]] == [1]


@pytest.mark.parametrize("changes", [None, {}, {"version": 2, "precise": True}, {"version": 1, "precise": "true"}])
def test_unrecognized_change_contract_preserves_legacy_parent_expansion(changes):
    row = marker(1, "US")
    row.change_set = changes
    plan = plan_changes([row], {"US", "US-AK"})
    assert plan["country_codes"] == ["US", "US-AK"]


def test_one_legacy_marker_keeps_mixed_scope_group_atomic():
    plan = plan_changes([marker(1, "US"), marker(2, "US", precise=False)], {"US", "US-AK"}, max_countries=1)
    assert plan["country_codes"] == ["US", "US-AK"]
    assert plan["dirty_ids"] == [1, 2]
    assert plan["oversized_legacy_scopes"] == ["US"]


def test_full_planning_removes_batch_limit_but_not_disabled_scope_exclusion():
    plan = plan_changes([marker(1, "US"), marker(2, "JP"), marker(3, "DISABLED")], {"US", "JP"}, max_countries=1, full=True)
    assert plan["country_codes"] == ["JP", "US"]
    assert plan["dirty_ids"] == [1, 2]
    assert plan["continuation_dirty_ids"] == []


@pytest.mark.parametrize("budget", [0, -1])
def test_invalid_country_budget_is_rejected(budget):
    with pytest.raises(ValueError, match="positive"):
        plan_changes([], set(), max_countries=budget)
