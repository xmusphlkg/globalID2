"""RSS input shares the public snapshot and excludes private report material."""
from src.services.situation_notices.feed_export import FEED_RECORD_LIMIT, build_feed_payload


def test_feed_orders_deduplicates_and_excludes_routine_and_private_fields():
    older = {"alert_id": "same", "published_date": "2026-10-01", "status": "new", "summary_en": "old"}
    newer = {**older, "published_date": "2026-11-01", "summary_en": "new", "host_type": "animal",
             "host_species": "wild birds", "count_unit": "animals", "raw_text": "PRIVATE",
             "quote": "QUOTE", "ai": {"private": True}, "host_evidence": "EVIDENCE"}
    routine = {"alert_id": "routine", "published_date": "2026-11-02", "status": "routine"}
    payload = build_feed_payload([older, newer, routine], generated_at="snapshot-time", as_of="2026-11-15")
    assert payload["schema_version"] == "gids.situation_alerts.v1"
    assert payload["generated_at"] == "snapshot-time"
    assert payload["as_of"] == "2026-11-15"
    assert len(payload["alerts"]) == 1
    alert = payload["alerts"][0]
    assert alert["summary_en"] == "new"
    assert alert["host_type"] == "animal" and alert["host_species"] == "wild birds"
    assert not {"raw_text", "quote", "ai", "host_evidence"} & alert.keys()


def test_global_feed_is_bounded_after_identity_deduplication():
    records = [{"alert_id": f"a-{i:04}", "published_date": "2026-11-01", "status": "ongoing"}
               for i in range(FEED_RECORD_LIMIT + 20)]
    payload = build_feed_payload(records + records, generated_at="snapshot-time", as_of="2026-11-15")
    assert len(payload["alerts"]) == FEED_RECORD_LIMIT
    assert payload["alerts"][0]["alert_id"] == "a-0219"
    assert payload["alerts"][-1]["alert_id"] == "a-0020"


def test_valid_empty_feed_retains_snapshot_identity_without_invented_events():
    assert build_feed_payload([], generated_at="snapshot-time", as_of="2026-11-15") == {
        "schema_version": "gids.situation_alerts.v1", "generated_at": "snapshot-time",
        "as_of": "2026-11-15", "alerts": [],
    }
