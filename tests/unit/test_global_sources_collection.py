"""Unit tests for the international authority collection envelope."""

from __future__ import annotations

from datetime import date

import pytest

from src.services.global_sources import collection
from src.services.global_sources.catalogue import iso_week


def _event(
    *,
    source_id: str = "who_don",
    native_id: str,
    published: date | None,
    fingerprint: str | None = None,
) -> dict:
    week = iso_week(published) if published else {
        "iso_year": None,
        "iso_week": None,
        "iso_week_key": None,
    }
    return {
        "event_id": f"{source_id}:{native_id}",
        "fingerprint": fingerprint or f"{source_id}:{native_id}",
        "source_id": source_id,
        "published_date": published.isoformat() if published else None,
        **week,
    }


@pytest.fixture(autouse=True)
def _restore_loaders():
    original = collection.SOURCE_LOADERS
    collection.SOURCE_LOADERS = dict(original)
    yield
    collection.SOURCE_LOADERS = original


def test_window_is_derived_from_since_days():
    envelope = collection.collect(since_days=10, today=date(2026, 10, 27), sources=[])
    assert envelope["since_date"] == "2026-10-17"
    assert envelope["as_of"] == "2026-10-27"
    assert envelope["events"] == []
    assert envelope["sources"] == []


def test_records_are_sorted_newest_first_and_window_trimmed():
    collection.SOURCE_LOADERS = {
        "who_don": lambda **_: [
            _event(native_id="old", published=date(2026, 1, 5)),
            _event(native_id="new", published=date(2026, 10, 20)),
            _event(native_id="mid", published=date(2026, 9, 1)),
        ],
    }
    envelope = collection.collect(since_days=120, today=date(2026, 10, 27))
    assert [row["event_id"] for row in envelope["events"]] == [
        "who_don:new",
        "who_don:mid",
    ]
    status = envelope["sources"][0]
    assert status["event_count"] == 2


def test_undated_records_are_reported_but_dropped():
    collection.SOURCE_LOADERS = {
        "who_don": lambda **_: [
            _event(native_id="dated", published=date(2026, 10, 20)),
            _event(native_id="undated", published=None),
        ],
        "who_wer": lambda **_: [_event(source_id="who_wer", native_id="u", published=None)],
    }
    envelope = collection.collect(since_days=120, today=date(2026, 10, 27))
    assert [row["event_id"] for row in envelope["events"]] == ["who_don:dated"]
    by_source = {row["source_id"]: row for row in envelope["sources"]}
    assert by_source["who_don"]["undated_records"] == 1
    assert by_source["who_wer"]["undated_records"] == 1
    assert by_source["who_wer"]["event_count"] == 0


def test_duplicate_fingerprints_are_kept_once():
    collection.SOURCE_LOADERS = {
        "who_don": lambda **_: [
            _event(native_id="a", published=date(2026, 10, 20), fingerprint="shared"),
            _event(native_id="b", published=date(2026, 10, 21), fingerprint="shared"),
        ],
    }
    envelope = collection.collect(since_days=120, today=date(2026, 10, 27))
    assert len(envelope["events"]) == 1


def test_one_failing_source_does_not_abort_the_run():
    def _boom(**_: object):
        raise RuntimeError("hub offline")

    collection.SOURCE_LOADERS = {
        "who_don": _boom,
        "who_wer": lambda **_: [
            _event(source_id="who_wer", native_id="issue", published=date(2026, 10, 18)),
        ],
    }
    envelope = collection.collect(since_days=120, today=date(2026, 10, 27))
    by_source = {row["source_id"]: row for row in envelope["sources"]}
    assert by_source["who_don"]["status"] == "failed"
    assert "hub offline" in by_source["who_don"]["error"]
    assert by_source["who_wer"]["status"] == "ok"
    assert [row["event_id"] for row in envelope["events"]] == ["who_wer:issue"]


def test_unregistered_and_blocked_sources_are_skipped(monkeypatch):
    monkeypatch.setitem(
        collection.SOURCE_REGISTRY,
        "blocked_source",
        {"source_id": "blocked_source", "redistribution": "blocked"},
    )
    envelope = collection.collect(since_days=30, today=date(2026, 10, 27), sources=["blocked_source", "nope"])
    statuses = {row["source_id"]: row for row in envelope["sources"]}
    assert statuses["blocked_source"]["status"] == "skipped"
    assert statuses["nope"]["error"] == "no adapter registered"
