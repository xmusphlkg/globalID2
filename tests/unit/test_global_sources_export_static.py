"""Unit tests for the static export of international authority notices."""

from __future__ import annotations

import json
from typing import Any

from src.services.global_sources.export_static import SCHEMA_VERSION, export_events


def _run(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": "2026-10-27T00:00:00+00:00",
        "since_days": 120,
        "since_date": "2026-06-29",
        "as_of": "2026-10-27",
        "sources": [
            {
                "source_id": "who_don",
                "label_en": "WHO Disease Outbreak News",
                "status": "ok",
                "event_count": len(events),
                "undated_records": 0,
                "error": None,
            }
        ],
        "events": events,
    }


def _event(native_id: str, published: str, iso_year: int, week: int, *, source_id: str = "who_don") -> dict[str, Any]:
    return {
        "event_id": f"{source_id}:{native_id}",
        "fingerprint": f"{source_id}:{native_id}",
        "source_id": source_id,
        "native_id": native_id,
        "record_type": "outbreak_notice",
        "title_en": f"Notice {native_id}",
        "title_zh": f"通报 {native_id}",
        "summary_en": "Body text suppressed for this test.",
        "url": f"https://example.org/{native_id}",
        "source_label_en": "WHO Disease Outbreak News",
        "source_label_zh": "世卫组织疾病暴发新闻",
        "published_date": published,
        "iso_year": iso_year,
        "iso_week": week,
        "iso_week_key": f"{iso_year}-W{week:02d}",
        "diseases": [{"disease_id": "D002", "name_en": "Cholera", "name_zh": "霍乱", "slug": "cholera"}],
        "countries": [{"code": "KE", "name_en": "Kenya", "name_zh": "肯尼亚"}],
    }


def test_latest_carries_schema_window_and_weeks(tmp_path):
    summary = export_events(_run([_event("a", "2026-10-20", 2026, 43)]), output_dir=tmp_path)
    assert summary["event_count"] == 1
    assert summary["disease_shards"] == 1
    assert summary["country_shards"] == 1

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["schema_version"] == SCHEMA_VERSION
    assert latest["window"] == {"since_days": 120, "since_date": "2026-06-29", "as_of": "2026-10-27"}
    assert latest["iso_weeks"][0]["iso_week_key"] == "2026-W43"
    assert latest["diseases"][0]["event_count"] == 1
    assert latest["countries"][0]["code"] == "KE"
    # Recent entries keep every documented field; nothing extra leaks through.
    assert latest["recent"][0]["title_en"] == "Notice a"
    assert "internal_note" not in latest["recent"][0]


def test_reference_shards_are_slimmer_than_the_archive(tmp_path):
    export_events(_run([_event("a", "2026-10-20", 2026, 43)]), output_dir=tmp_path)
    archive = json.loads((tmp_path / "archive" / "2026.json").read_text(encoding="utf-8"))
    shard = json.loads((tmp_path / "by-disease" / "D002.json").read_text(encoding="utf-8"))
    assert archive["event_count"] == 1
    assert shard["event_count"] == 1
    assert shard["disease"]["slug"] == "cholera"
    # Shards carry the (peer-reviewed) summaries the panels render, but not
    # provenance fields that only the archive needs.
    assert "summary_en" in shard["events"][0]
    assert "retrieved_at" not in shard["events"][0]
    assert "fingerprint" not in shard["events"][0]
    assert "native_id" not in shard["events"][0]


def test_recent_limit_is_honoured_and_files_are_rebuilt(tmp_path):
    (tmp_path / "by-disease").mkdir(parents=True)
    stale = tmp_path / "by-disease" / "STALE.json"
    stale.write_text("{}", encoding="utf-8")

    summary = export_events(
        _run([_event("a", "2026-10-20", 2026, 43), _event("b", "2026-09-01", 2026, 36)]),
        output_dir=tmp_path,
        recent_limit=1,
    )
    assert summary["recent_event_count"] == 1
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert [row["event_id"] for row in latest["recent"]] == ["who_don:a"]
    assert not stale.exists(), "stale shards must be cleared instead of accumulating"


def test_sources_json_merges_registry_metadata_with_run_status(tmp_path):
    export_events(_run([_event("a", "2026-10-20", 2026, 43)]), output_dir=tmp_path)
    payload = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    source = payload["sources"][0]
    assert source["status"] == "ok"
    assert source["source_id"] == "who_don"
    assert source["homepage"], "registry metadata should ride along for attribution"


def test_undated_events_still_land_in_the_export(tmp_path):
    event = _event("u", "", None, 0)
    event.update({"published_date": None, "iso_year": None, "iso_week": None, "iso_week_key": None})
    export_events(_run([event]), output_dir=tmp_path)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["event_count"] == 1
    assert latest["iso_weeks"] == []
    assert (tmp_path / "by-country" / "KE.json").exists()
