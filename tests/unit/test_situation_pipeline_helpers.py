"""Pure-helper tests for the alert pipeline: hashing, ids and outbreak grouping."""

from __future__ import annotations

from datetime import date

from src.services.situation_notices.pipeline import alert_id, cluster_alerts, content_hash, disease_key

AS_OF = date(2026, 10, 28)


def _alert(aid, notice, day, country, *, disease="D050", label="Ebola", status="ongoing", source="who_don") -> dict:
    return {
        "alert_id": aid,
        "notice_id": notice,
        "source_id": source,
        "published_date": day,
        "disease_id": disease,
        "disease_label": label,
        "country_code": country,
        "status": status,
    }


def test_content_hash_covers_catalogue_fields_only():
    base = {
        "title_en": "T",
        "summary_en": "S",
        "published_date": "2026-10-23",
        "updated_at_source": "2026-10-23",
        "url": "https://x/y",
        "document_ref": {"kind": "who_don", "slug": "a"},
    }
    assert content_hash(base) == content_hash({**base, "retrieved_at": "now", "fingerprint": "zz"})
    assert content_hash(base) != content_hash({**base, "title_en": "T2"})
    assert content_hash(base) != content_hash({**base, "document_ref": {"kind": "who_don", "slug": "b"}})


def test_disease_key_prefers_catalogue_id_then_normalised_label():
    assert disease_key("D050", "Whatever") == "D050"
    assert disease_key(None, "Nipah  Virus") == disease_key(None, "nipah virus")
    assert disease_key(None, "Nipah virus").startswith("label:")


def test_alert_id_is_stable_and_separates_places():
    a = alert_id("n1", "D050", "KE", None)
    assert a == alert_id("n1", "D050", "KE", None)
    assert a == alert_id("n1", "D050", "KE", "  ")
    assert a != alert_id("n1", "D050", "UG", None)
    assert a != alert_id("n1", "D050", "KE", "Nairobi")
    assert a != alert_id("n2", "D050", "KE", None)
    assert len(a) == 16


def test_one_notice_naming_three_countries_is_one_outbreak_with_three_alerts():
    rows = [
        _alert("1", "who_don:622", date(2026, 10, 23), "CD"),
        _alert("2", "who_don:622", date(2026, 10, 23), "KE", status="imported"),
        _alert("3", "who_don:622", date(2026, 10, 23), "NG", status="imported"),
    ]
    (outbreak,) = cluster_alerts(rows, as_of=AS_OF)
    assert outbreak["country_codes"] == ["CD", "KE", "NG"]
    assert outbreak["alert_ids"] == ["1", "2", "3"]
    assert outbreak["notice_ids"] == ["who_don:622"]
    assert outbreak["status"] == "active"
    assert outbreak["disease_id"] == "D050"


def test_later_notices_for_a_known_country_join_the_same_outbreak():
    rows = [
        _alert("1", "n1", date(2026, 10, 1), "CD"),
        _alert("2", "n1", date(2026, 10, 1), "KE"),
        _alert("3", "n2", date(2026, 10, 20), "KE", source="africa_cdc"),
    ]
    (outbreak,) = cluster_alerts(rows, as_of=AS_OF)
    assert outbreak["notice_ids"] == ["n1", "n2"]
    assert outbreak["source_ids"] == ["africa_cdc", "who_don"]
    assert outbreak["first_published"] == date(2026, 10, 1)
    assert outbreak["last_published"] == date(2026, 10, 20)


def test_same_country_gap_beyond_the_limit_splits_outbreaks():
    rows = [
        _alert("1", "n1", date(2026, 3, 1), "KE"),
        _alert("2", "n2", date(2026, 10, 1), "KE"),
    ]
    outbreaks = cluster_alerts(rows, as_of=AS_OF)
    assert len(outbreaks) == 2
    status = {o["first_published"]: o["status"] for o in outbreaks}
    assert status[date(2026, 3, 1)] == "quiet"
    assert status[date(2026, 10, 1)] == "active"


def test_different_countries_in_different_notices_stay_separate():
    rows = [
        _alert("1", "n1", date(2026, 10, 1), "KE"),
        _alert("2", "n2", date(2026, 10, 2), "UG"),
    ]
    assert len(cluster_alerts(rows, as_of=AS_OF)) == 2


def test_different_diseases_never_merge_even_in_one_notice():
    rows = [
        _alert("1", "n1", date(2026, 10, 1), "KE"),
        _alert("2", "n1", date(2026, 10, 1), "KE", disease="D038", label="Influenza"),
    ]
    assert {o["disease_id"] for o in cluster_alerts(rows, as_of=AS_OF)} == {"D050", "D038"}


def test_routine_alerts_are_not_outbreaks():
    rows = [_alert("1", "n1", date(2026, 10, 20), "FR", status="routine")]
    assert cluster_alerts(rows, as_of=AS_OF) == []


def test_ended_in_every_place_makes_a_recent_outbreak_quiet():
    rows = [
        _alert("1", "n1", date(2026, 10, 20), "UG", status="ongoing"),
        _alert("2", "n2", date(2026, 10, 27), "UG", status="ended"),
    ]
    (outbreak,) = cluster_alerts(rows, as_of=AS_OF)
    assert outbreak["status"] == "quiet"


def test_ended_in_one_place_only_keeps_the_outbreak_active():
    rows = [
        _alert("1", "n1", date(2026, 10, 20), "UG", status="ongoing"),
        _alert("2", "n1", date(2026, 10, 20), "KE", status="ongoing"),
        _alert("3", "n2", date(2026, 10, 27), "UG", status="ended"),
    ]
    (outbreak,) = cluster_alerts(rows, as_of=AS_OF)
    assert outbreak["status"] == "active"


def test_outbreak_id_survives_later_additions():
    first = [_alert("1", "n1", date(2026, 10, 1), "KE")]
    grown = first + [_alert("2", "n2", date(2026, 10, 20), "KE")]
    assert cluster_alerts(first, as_of=AS_OF)[0]["outbreak_id"] == cluster_alerts(grown, as_of=AS_OF)[0]["outbreak_id"]


def test_unlisted_disease_groups_by_label():
    rows = [
        _alert("1", "n1", date(2026, 10, 1), "IN", disease=None, label="Nipah virus"),
        _alert("2", "n2", date(2026, 10, 9), "IN", disease=None, label="NIPAH  virus"),
    ]
    (outbreak,) = cluster_alerts(rows, as_of=AS_OF)
    assert outbreak["disease_id"] is None
    assert outbreak["disease_label"] == "Nipah virus"


def test_a_roundup_naming_many_countries_does_not_make_one_outbreak():
    rows = [
        {**_alert(str(i), "ecdc_cdtr:w42", date(2026, 10, 16), cc, disease="D128"), "document_type": "monitoring_product"}
        for i, cc in enumerate(["DE", "IT", "FR"])
    ]
    assert len(cluster_alerts(rows, as_of=AS_OF)) == 3


def test_an_ended_place_does_not_join_a_later_outbreak_through_a_roundup():
    rows = [
        _alert("ug-1", "who_don:600", date(2026, 6, 1), "UG"),
        _alert("ug-end", "who_don:601", date(2026, 7, 20), "UG", status="ended"),
        {**_alert("ug-wer", "who_wer:1", date(2026, 8, 1), "UG", status="ended"), "document_type": "monitoring_product"},
        {**_alert("cd-wer", "who_wer:1", date(2026, 8, 1), "CD"), "document_type": "monitoring_product"},
    ]
    outbreaks = cluster_alerts(rows, as_of=AS_OF)
    by_alert = {aid: o["outbreak_id"] for o in outbreaks for aid in o["alert_ids"]}
    assert by_alert["ug-1"] == by_alert["ug-end"]
    assert by_alert["cd-wer"] != by_alert["ug-1"]
