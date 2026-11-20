"""Precise invalidation must preserve every changed article or fall back."""
import pytest

from src.literature.publication_changes import (
    build_literature_change_set, mark_literature_dirty,
    merge_literature_change_sets, validate_literature_change_set,
)


def test_merge_retains_concurrent_revisions_and_all_article_kinds():
    first = build_literature_change_set(["b", "a", "a"], change_kind="summary",
        before_fingerprints={"a": "1" * 64}, after_fingerprints={"a": "2" * 64})
    second = build_literature_change_set(["a"], change_kind="classification")
    merged = merge_literature_change_sets([first, second, first])
    assert merged["article_ids"] == ["a", "b"]
    assert len(merged["changes"]) == 3
    assert {change["change_kind"] for change in merged["changes"]} == {"summary", "classification"}
    summary = next(change for change in merged["changes"] if change["article_id"] == "a" and change["change_kind"] == "summary")
    assert summary["before_fingerprint"] == "1" * 64
    assert summary["after_fingerprint"] == "2" * 64


@pytest.mark.parametrize("unknown", [None, {}, {"version": 1, "precise": True},
    {"version": True, "precise": True, "line": "literature", "article_ids": [], "changes": []}])
def test_unknown_marker_never_narrows_export(unknown):
    assert merge_literature_change_sets([build_literature_change_set(["known"]), unknown]) is None


def test_incomplete_article_coverage_and_invalid_fingerprints_are_rejected():
    value = build_literature_change_set(["a"])
    value["article_ids"].append("unaccounted")
    with pytest.raises(ValueError, match="cover"):
        validate_literature_change_set(value)
    with pytest.raises(ValueError, match="fingerprints"):
        build_literature_change_set(["a"], after_fingerprints={"a": "invalid"})


async def test_empty_known_scope_is_noop_and_unknown_scope_remains_coarse():
    class DB:
        def __init__(self):
            self.rows = []
        def add(self, row):
            self.rows.append(row)
    db = DB()
    await mark_literature_dirty(db, task_uuid="test", article_ids=[])
    assert db.rows == []
    await mark_literature_dirty(db, task_uuid="test", article_ids=["article"])
    await mark_literature_dirty(db, task_uuid="test")
    assert db.rows[0].change_set["article_ids"] == ["article"]
    assert db.rows[1].change_set is None
