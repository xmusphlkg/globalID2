"""Offline tests for the public shape of the Situation export."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from src.domain import SituationAlert, SituationExtraction, SituationNotice
from src.services.situation_notices.export import alert_record, facets, latest_per_place, map_marker, weekly_changes

SECRET_BODY = "INTERNAL-ONLY full report paragraph that must never be published."


def _notice(redistribution="metadata_and_extract", source="who_don") -> SituationNotice:
    return SituationNotice(
        notice_id=f"{source}:1",
        source_id=source,
        native_id="1",
        record_type="outbreak_notice",
        title_en="Ebola - DRC, Kenya",
        url="https://www.who.int/emergencies/disease-outbreak-news/item/1",
        redistribution=redistribution,
        published_date=date(2026, 10, 23),
        iso_week_key="2026-W43",
        content_hash="h",
        first_seen_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )


def _alert(**overrides) -> SituationAlert:
    values = dict(
        alert_id="a1",
        notice_id="who_don:1",
        extraction_id=1,
        outbreak_id="OB-1",
        disease_id="D050",
        disease_label="Bundibugyo virus disease",
        country_code="KE",
        admin_area="Nairobi",
        status="imported",
        cases=9,
        deaths=1,
        count_basis="confirmed",
        as_of=date(2026, 10, 20),
        quote="Nine cases including one death have been reported in Kenya.",
        summary_en="Nine cases in Kenya.",
        summary_zh="肯尼亚9例。",
        summary_fr="Neuf cas au Kenya.",
        published_date=date(2026, 10, 23),
        source_id="who_don",
    )
    values.update(overrides)
    return SituationAlert(**values)


def _extraction() -> SituationExtraction:
    return SituationExtraction(
        notice_id="who_don:1",
        document_hash="d",
        prompt_version="v1",
        status="published",
        input_mode="native_file",
        author_model="a",
        reviewer_model="b",
        review_verdict="accept",
    )


DISEASES = {"D050": {"disease_id": "D050", "name_en": "Ebola", "name_zh": "埃博拉", "slug": "ebola"}}


def test_public_record_has_facts_quote_and_link_but_no_document_text():
    record = alert_record(_alert(), _notice(), _extraction(), DISEASES)
    assert record["cases"] == 9 and record["deaths"] == 1 and record["as_of"] == "2026-10-20"
    assert record["quote"].startswith("Nine cases")
    assert record["url"].startswith("https://www.who.int/")
    assert record["country_name_en"] and record["disease_slug"] == "ebola"
    assert record["ai"] == {
        "review_mode": "cross_model",
        "input_mode": "native_file",
        "verdict": "accept",
        "prompt_version": "v1",
    }
    notice = _notice()
    notice.summary_en = SECRET_BODY
    assert SECRET_BODY not in json.dumps(alert_record(_alert(), notice, _extraction(), DISEASES), ensure_ascii=False)


def test_quote_is_published_for_every_source_except_blocked_ones():
    for terms in ("metadata_and_extract", "metadata_only", ""):
        record = alert_record(_alert(), _notice(redistribution=terms), _extraction(), DISEASES)
        assert record["quote"]
    blocked = alert_record(_alert(), _notice(redistribution="blocked"), _extraction(), DISEASES)
    assert blocked["quote"] is None
    # Structured facts and the link stay public either way.
    assert blocked["cases"] == 9 and blocked["url"]


def test_unlisted_disease_falls_back_to_the_reports_label():
    record = alert_record(_alert(disease_id=None, disease_label="Nipah virus"), _notice(), None, DISEASES)
    assert record["disease_name_en"] == "Nipah virus"
    assert record["disease_slug"] is None and record["ai"] is None


def _rec(aid, day, outbreak="OB-1", country="KE", area=None, disease="D050", source="who_don") -> dict:
    return {
        "alert_id": aid,
        "outbreak_id": outbreak,
        "published_date": day,
        "country_code": country,
        "admin_area": area,
        "disease_id": disease,
        "disease_label": "x",
        "source_id": source,
        "source_label_en": source,
        "source_label_zh": source,
        "disease_name_en": "Ebola",
        "disease_name_zh": None,
        "disease_slug": None,
        "country_name_en": country,
        "country_name_zh": None,
    }


def test_latest_per_place_keeps_the_newest_alert_of_each_outbreak_and_place():
    records = [
        _rec("old-ke", "2026-10-01"),
        _rec("new-ke", "2026-10-20"),
        _rec("cd", "2026-10-15", country="CD"),
        _rec("ke-nairobi", "2026-10-18", area="Nairobi"),
        _rec("other-outbreak", "2026-10-19", outbreak="OB-2"),
    ]
    points = latest_per_place(records)
    assert [p["alert_id"] for p in points] == ["new-ke", "other-outbreak", "ke-nairobi", "cd"]


def test_facets_count_points_per_value_most_common_first():
    points = [
        _rec("1", "2026-10-01", country="KE"),
        _rec("2", "2026-10-02", country="KE", source="africa_cdc"),
        _rec("3", "2026-10-03", country="CD"),
    ]
    result = facets(points)
    assert [(c["id"], c["count"]) for c in result["countries"]] == [("KE", 2), ("CD", 1)]
    assert {s["id"]: s["count"] for s in result["sources"]} == {"who_don": 2, "africa_cdc": 1}
    assert result["diseases"][0]["count"] == 3


def test_weekly_changes_counts_new_updated_and_first_seen_places():
    records = [
        _rec("1", "2026-10-06", country="CD"),  # ISO week 41
        _rec("2", "2026-10-14", country="CD"),  # week 42
        _rec("3", "2026-10-14", country="KE"),  # week 42, new place
    ]
    outbreak = {"outbreak_id": "OB-1", "first_published": "2026-10-06", "alerts": records}
    weeks = weekly_changes(records, [outbreak])
    assert list(weeks) == ["2026-W42", "2026-W41"]
    assert weeks["2026-W41"]["new_outbreaks"] == ["OB-1"] and weeks["2026-W41"]["new_places"] == 1
    assert weeks["2026-W42"]["updated_outbreaks"] == ["OB-1"]
    assert weeks["2026-W42"]["new_places"] == 1 and weeks["2026-W42"]["alerts"] == 2


def test_map_marker_keeps_place_labels_and_drops_alert_prose():
    marker = map_marker(
        {
            "alert_id": "a1",
            "outbreak_id": "OB-1",
            "source_id": "who_don",
            "source_label_en": "WHO Disease Outbreak News",
            "source_label_zh": "世卫组织疾病暴发新闻",
            "disease_id": "D050",
            "disease_slug": "ebola",
            "disease_label": "Ebola",
            "disease_name_en": "Ebola virus disease",
            "disease_name_zh": "埃博拉病毒病",
            "country_code": "KE",
            "country_name_en": "Kenya",
            "country_name_zh": "肯尼亚",
            "admin_area": None,
            "status": "ongoing",
            "published_date": "2026-10-23",
            "quote": "nine cases",
            "summary_en": "Kenya reported nine cases.",
            "summary_zh": "肯尼亚报告9例。",
            "summary_fr": "Le Kenya a signalé neuf cas.",
            "ai": {"verdict": "accept"},
            "url": "https://www.who.int/item/1",
            "cases": 9,
            "deaths": 1,
        }
    )
    assert marker["disease_name_zh"] == "埃博拉病毒病"
    assert marker["published_date"] == "2026-10-23"
    assert "admin_area" not in marker
    assert set(marker).isdisjoint({"quote", "summary_en", "summary_zh", "summary_fr", "ai", "url", "cases", "deaths"})


def test_map_point_prefers_the_recent_alert_with_counts_over_a_newer_empty_statement():
    with_counts = {**_rec("who-ke", "2026-10-23"), "cases": 9, "deaths": 1, "quote": "nine cases"}
    empty = {**_rec("acdc-ke", "2026-10-27"), "cases": None, "deaths": None, "quote": None}
    (point,) = latest_per_place([with_counts, empty])
    assert point["alert_id"] == "who-ke"
    assert point["place_updated"] == "2026-10-27"
    assert point["place_alert_count"] == 2


def test_reporting_period_exports_annual_and_incident_intervals_but_rejects_conflicting_evidence():
    annual = alert_record(_alert(
        quote='Since the beginning of 2026, and as of 20 October 2026, nine cases were reported in Kenya.',
    ), _notice(), _extraction(), DISEASES)
    assert annual['reporting_period'] == {'kind': 'cumulative', 'start': '2026-01-01', 'end': '2026-10-20'}
    incident = alert_record(_alert(
        quote='Nine new cases were reported from 2026-10-14 to 2026-10-20 in Kenya.',
    ), _notice(), _extraction(), DISEASES)
    assert incident['reporting_period'] == {'kind': 'incident', 'start': '2026-10-14', 'end': '2026-10-20'}
    for quote in (
        'Since the beginning of 2026, nine cases were reported, including two new cases in the past 7 days.',
        'Since the beginning of 2026, nine cases were reported, compared with three in 2025.',
        'Nine new cases were reported from 2026-10-22 to 2026-10-20 in Kenya.',
        'Nine new cases have been reported in Kenya.',
    ):
        record = alert_record(_alert(quote=quote,
            summary_en='Since the beginning of 2026, nine cases were reported.'), _notice(), _extraction(), DISEASES)
        assert 'reporting_period' not in record


def test_reporting_period_prefers_quote_and_can_tag_blocked_quote_without_publishing_text():
    quote = 'Between 1 January and 20 October 2026, nine cases were reported in Kenya.'
    record = alert_record(_alert(quote=quote, summary_en='Six new cases were reported since the previous update.'),
        _notice(redistribution='blocked'), _extraction(), DISEASES)
    assert record['quote'] is None
    assert record['reporting_period'] == {'kind': 'cumulative', 'start': '2026-01-01', 'end': '2026-10-20'}
    # A date-bearing source quote may never silently inherit a conflicting
    # annual period from an AI summary.
    record = alert_record(_alert(quote='On 20 October 2026, nine cases were reported in Kenya.',
        summary_en='Since the beginning of 2026, nine cases were reported.'), _notice(), _extraction(), DISEASES)
    assert 'reporting_period' not in record


def test_monitoring_tags_routine_facts_and_generic_roundup_topic_headings_without_private_text():
    from src.domain import SituationDocument
    from src.services.situation_notices.coverage import monitoring_coverage
    notice = _notice()
    notice.title_en = 'Weekly surveillance roundup'
    notice.document = SituationDocument(notice_id=notice.notice_id, ref={}, text=(
        '# Contents\n## Influenza – Japan\nOrdinary surveillance with nothing unusual.\n'
        f'{SECRET_BODY}\nBackground: travellers from Canada should consult their doctor.\n'
    ))
    coverage = monitoring_coverage(notice, [{
        'disease_id': 'D050', 'country_code': 'KE', 'status': 'routine',
    }])
    assert {'D050', 'D038'} <= set(coverage['disease_ids'])
    assert coverage['country_codes'] == ['JP', 'KE']
    assert SECRET_BODY not in json.dumps(coverage)
    assert 'CA' not in coverage['country_codes']


def test_monitoring_unknown_coverage_stays_omitted_and_explicit_ids_are_validated():
    from src.domain import SituationDocument
    from src.services.situation_notices.coverage import monitoring_coverage
    notice = _notice()
    notice.title_en = 'Weekly public health roundup'
    notice.document = SituationDocument(notice_id=notice.notice_id, ref={}, text='No unusual event was reported.')
    assert monitoring_coverage(notice, []) == {}
    notice.document.ref = {'disease_ids': ['D050', 'D-UNKNOWN', 10], 'country_codes': ['ke', 'UNKNOWN', 10]}
    assert monitoring_coverage(notice, []) == {'disease_ids': ['D050'], 'country_codes': ['KE']}
    notice.title_en = 'Middle East respiratory syndrome coronavirus – Saudi Arabia'
    assert monitoring_coverage(notice, []) == {'disease_ids': ['D049', 'D050'], 'country_codes': ['KE', 'SA']}



def test_annual_export_does_not_mistake_new_zealand_for_new_cases():
    record = alert_record(_alert(
        country_code='NZ',
        quote='Since the beginning of 2026, New Zealand reported nine cases including one death.',
    ), _notice(), _extraction(), DISEASES)
    assert record['reporting_period'] == {'kind': 'cumulative', 'start': '2026-01-01', 'end': '2026-10-20'}
    for mixed in (
        'Since the beginning of 2026, nine cases were reported; three cases were reported in the monthly update.',
        'Since the beginning of 2026, nine cases were reported; three since the previous update.',
        'Since the beginning of 2026, nine cases were reported, including three new laboratory-confirmed cases.',
        'Since the beginning of 2026, nine cases were reported, including three new MERS cases.',
    ):
        assert 'reporting_period' not in alert_record(_alert(quote=mixed), _notice(), _extraction(), DISEASES)


def test_monitoring_coverage_ignores_travel_background_and_publisher_sections():
    from src.domain import SituationDocument
    from src.services.situation_notices.coverage import monitoring_coverage
    notice = _notice()
    notice.title_en = 'Weekly surveillance roundup'
    notice.document = SituationDocument(notice_id=notice.notice_id, ref={}, text=(
        '## Influenza – Japan\nOrdinary surveillance.\n'
        '## Travel advice – Kenya\nConsult a doctor.\n'
        '## Historical Ebola in Canada\nPast outbreaks.\n'
        '## Contact WHO country office – Switzerland\nContact details.\n'
    ))
    assert monitoring_coverage(notice, []) == {'disease_ids': ['D038'], 'country_codes': ['JP']}
