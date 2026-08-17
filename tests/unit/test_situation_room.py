from __future__ import annotations

import pandas as pd

from src.domain.situation import PublicHealthEvent
from src.services.situation_room import analyze_frame, build_snapshot, load_config


def _weekly_frame(values: list[int]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "time": period,
            "value": value,
            "quality_status": "validated",
            "geography_key": "national",
            "series_code": "jp-influenza-weekly",
            "disease_id": "D038",
            "disease_name": "Influenza",
            "disease_slug": "influenza",
            "country_code": "JP",
            "country_name": "Japan",
            "source_label": "Test source",
            "temporal_granularity": "weekly",
        }
        for period, value in zip(pd.date_range("2019-01-07", periods=len(values), freq="W-MON"), values, strict=True)
    ])


def test_detects_confirmed_source_native_increase() -> None:
    signals = analyze_frame(_weekly_frame([10] * 176 + [35] * 4), load_config())

    assert len(signals) == 1
    assert signals[0]["window"]["label"] == "Last 4 weeks"
    assert signals[0]["window"]["periods"] == 4
    assert signals[0]["window"]["current_cases"] == 140
    assert signals[0]["window"]["previous_cases"] == 40
    assert signals[0]["window"]["absolute_change"] == 100
    assert signals[0]["window"]["change_pct"] == 250.0
    assert signals[0]["statistics"]["detector_votes"] >= 2
    assert signals[0]["risk"]["confidence"] == "low"
    assert signals[0]["cadence"] == "weekly"


def test_suppresses_low_base_percentage_artifact() -> None:
    signals = analyze_frame(_weekly_frame([0] * 176 + [1, 1, 1, 1]), load_config())

    assert signals == []


def test_snapshot_separates_statistical_and_official_event_evidence() -> None:
    config = load_config()
    signals = analyze_frame(_weekly_frame([10] * 176 + [35] * 4), config)
    event = {"id": "event:1", "kind": "official_event", "source": "who_don", "title": "Official event", "source_url": "https://example.test/event"}

    snapshot = build_snapshot(signals, [event], {"who_don": {"status": "fresh"}}, config)

    assert snapshot["increasing"][0]["kind"] == "statistical_signal"
    assert snapshot["emerging"] == [event]
    assert snapshot["coverage"]["note_en"].startswith("Statistical signals cover")
    assert snapshot["schema_version"] == "situation_room.v2"
    assert snapshot["checked_at"] == snapshot["content_updated_at"]


def test_public_health_event_serializes_mapped_metadata_column() -> None:
    event = PublicHealthEvent(
        source="who_don",
        external_id="event-1",
        source_url="https://example.test/event-1",
        title="Example event",
        content_hash="hash-1",
        metadata_={"source_kind": "official"},
    )

    document = event.to_dict()

    assert document["metadata"] == {"source_kind": "official"}
