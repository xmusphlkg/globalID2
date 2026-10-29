"""Offline tests for the alert extraction flow of the Situation module."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.ai.attachments import Attachment
from src.services.situation_notices.ai import (
    AIOutputError,
    Completion,
    contains_number,
    extract_alerts,
    normalise,
    review_mode,
    sanitize_extraction,
)

DOCUMENT = """\
## Summary
As of 20 October 2026, a total of 9 704 confirmed cases, including 4,687 deaths
(crude case fatality ratio: 48.3%), have been reported in the Democratic Republic of
the Congo. On 6 October 2026, the Ministry of Health of Kenya reported the first
laboratory-confirmed imported case of BVD. A total of nine cases including one death
has been reported in Kenya. On 20 October 2026, Nigeria confirmed an imported case in
Abuja. The out-
break remains active.
"""

NOTICE = {
    "notice_id": "who_don:2026-DON622",
    "source_id": "who_don",
    "source_label_en": "WHO Disease Outbreak News",
    "record_type": "outbreak_notice",
    "published_date": "2026-10-23",
    "title_en": "Ebola disease caused by Bundibugyo virus, DRC, Kenya and Nigeria",
}
DISEASES = [{"disease_id": "D050", "name_en": "Ebola"}, {"disease_id": "D038", "name_en": "Influenza"}]
COUNTRIES = {"CD", "KE", "NG", "UG"}
PUBLISHED = date(2026, 10, 23)


def _alert(**overrides) -> dict:
    base = {
        "disease_id": "D050",
        "disease_label": "Bundibugyo virus disease",
        "country_code": "CD",
        "admin_area": None,
        "status": "ongoing",
        "cases": 9704,
        "deaths": 4687,
        "count_basis": "confirmed",
        "as_of": "2026-10-20",
        "quote": "As of 20 October 2026, a total of 9 704 confirmed cases, including 4,687 deaths",
        "summary_en": "9704 confirmed cases and 4687 deaths in DRC.",
        "summary_zh": "刚果（金）确诊9704例。",
        "summary_fr": "9704 cas confirmés en RDC.",
    }
    base.update(overrides)
    return base


def _clean(*alerts, document_type="event_report", document=DOCUMENT):
    return sanitize_extraction(
        {"document_type": document_type, "alerts": list(alerts)},
        document_text=document,
        published=PUBLISHED,
        allowed_disease_ids={"D050", "D038"},
        allowed_country_codes=COUNTRIES,
    )


# --- sanitize_extraction ---


def test_a_supported_alert_passes_untouched():
    clean, notes = _clean(_alert())
    assert notes == []
    assert clean["document_type"] == "event_report"
    (alert,) = clean["alerts"]
    assert (alert["cases"], alert["deaths"], alert["as_of"]) == (9704, 4687, "2026-10-20")


def test_quote_must_appear_in_the_document():
    clean, notes = _clean(_alert(quote="Cases are rising fast across the whole country."))
    assert clean["alerts"] == []
    assert any("quote not found" in note for note in notes)


def test_quote_matching_ignores_case_dashes_quotes_and_hyphenation():
    quote = "The OUT‑BREAK remains active."  # non-breaking hyphen, upper case
    clean, _ = _clean(_alert(quote=quote))
    assert len(clean["alerts"]) == 1


def test_missing_quote_drops_the_alert():
    clean, notes = _clean(_alert(quote=None))
    assert clean["alerts"] == []
    assert notes


@pytest.mark.parametrize("value", [9704, "9704", "9 704", "9,704", 9704.0])
def test_number_forms_in_the_document_are_accepted(value):
    clean, notes = _clean(_alert(cases=value))
    assert clean["alerts"][0]["cases"] == 9704
    assert not any("cases" in note for note in notes)


def test_number_missing_from_the_document_is_cleared_but_alert_kept():
    clean, notes = _clean(_alert(cases=12345, deaths=4687))
    alert = clean["alerts"][0]
    assert alert["cases"] is None and alert["deaths"] == 4687
    assert any("cases=12345 not found" in note for note in notes)


def test_number_inside_a_longer_number_does_not_count():
    # 704 is only a substring of 9 704; it must not be accepted.
    clean, _ = _clean(_alert(cases=704, deaths=None))
    assert clean["alerts"][0]["cases"] is None


def test_decimal_fragment_does_not_count_as_a_number():
    # "48.3%" contains 3, but only as the decimal part.
    clean, _ = _clean(_alert(cases=None, deaths=3, quote="A total of nine cases including one death"))
    assert clean["alerts"][0]["deaths"] is None


def test_non_numeric_counts_are_cleared():
    clean, notes = _clean(_alert(cases="many", deaths=True))
    alert = clean["alerts"][0]
    assert alert["cases"] is None and alert["deaths"] is None
    assert any("not a count" in note for note in notes)


def test_count_basis_is_dropped_without_a_count():
    clean, _ = _clean(_alert(cases=None, deaths=None, count_basis="confirmed"))
    assert clean["alerts"][0]["count_basis"] is None


def test_unknown_country_drops_the_alert():
    clean, notes = _clean(_alert(country_code="EU"), _alert(country_code=""))
    assert clean["alerts"] == []
    assert sum("unknown country_code" in note for note in notes) == 2


def test_country_code_is_case_insensitive():
    clean, _ = _clean(_alert(country_code="cd"))
    assert clean["alerts"][0]["country_code"] == "CD"


def test_unknown_disease_id_is_cleared_but_the_label_survives():
    clean, notes = _clean(_alert(disease_id="D999"))
    assert clean["alerts"][0]["disease_id"] is None
    assert clean["alerts"][0]["disease_label"] == "Bundibugyo virus disease"
    assert any("D999" in note for note in notes)


def test_invalid_status_drops_the_alert():
    clean, notes = _clean(_alert(status="exploding"))
    assert clean["alerts"] == []
    assert any("invalid status" in note for note in notes)


def test_alert_without_a_label_or_summary_is_dropped():
    clean, _ = _clean(_alert(disease_label=""), _alert(country_code="KE", summary_en=None))
    assert clean["alerts"] == []


def test_duplicate_place_is_dropped_but_other_admin_areas_stay():
    clean, notes = _clean(_alert(), _alert(), _alert(admin_area="Ituri"))
    assert [a["admin_area"] for a in clean["alerts"]] == [None, "Ituri"]
    assert any("duplicate" in note for note in notes)


@pytest.mark.parametrize("as_of", ["2026-10-25", "2024-01-01", "not a date"])
def test_as_of_outside_the_reporting_window_is_cleared(as_of):
    clean, notes = _clean(_alert(as_of=as_of))
    assert clean["alerts"][0]["as_of"] is None
    assert any("as_of" in note for note in notes)


def test_as_of_one_day_after_publication_is_tolerated():
    clean, _ = _clean(_alert(as_of="2026-10-24"))
    assert clean["alerts"][0]["as_of"] == "2026-10-24"


def test_alert_count_is_capped():
    # distinct admin areas keep each alert unique; the quote is shared.
    alerts = [_alert(admin_area=f"Zone {i}", cases=None, deaths=None) for i in range(60)]
    clean, notes = _clean(*alerts)
    assert len(clean["alerts"]) == 40
    assert any("first 40" in note for note in notes)


def test_invalid_document_type_becomes_none():
    clean, _ = _clean(document_type="novel")
    assert clean["document_type"] is None


# --- normalise / contains_number ---


def test_normalise_joins_digit_groups_and_wrapped_words():
    assert normalise("9 704 and 1,234,567") == "9704 and 1234567"
    assert normalise("out-\nbreak") == "outbreak"


def test_contains_number_respects_boundaries():
    text = normalise("cases: 9 704, rate 48.3%")
    assert contains_number(text, 9704)
    assert not contains_number(text, 704)
    assert not contains_number(text, 3)
    assert contains_number(text, 48) is False


# --- extract_alerts with a scripted two-model review ---


class ScriptedCompleter:
    def __init__(self, author, reviewer, *, author_model="model-a", reviewer_model="model-b", input_mode="text"):
        self.script = {"author": list(author), "reviewer": list(reviewer)}
        self.models = {"author": author_model, "reviewer": reviewer_model}
        self.input_mode = input_mode
        self.calls: list[tuple[str, str, list]] = []

    async def __call__(self, role, system, prompt, *, attachments=None):
        self.calls.append((role, prompt, list(attachments or [])))
        reply = self.script[role].pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return Completion(text=text, model=self.models[role], input_mode=self.input_mode)


def _draft(*alerts, document_type="event_report"):
    return {"document_type": document_type, "alerts": list(alerts)}


def _review(verdict, *, issues=None, corrected=None, score=0.9):
    return {"verdict": verdict, "score": score, "issues": issues or [], "corrected": corrected}


ATTACHMENT = Attachment(name="don.txt", text=DOCUMENT, media_type="text/html")


async def _run(completer):
    return await extract_alerts(
        completer, NOTICE, ATTACHMENT, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES
    )


async def test_three_countries_become_three_published_alerts():
    draft = _draft(
        _alert(),
        _alert(
            country_code="KE",
            status="imported",
            cases=9,
            deaths=1,
            as_of="2026-10-20",
            quote="A total of nine cases including one death\nhas been reported in Kenya.",
        ),
        _alert(
            country_code="NG",
            status="imported",
            cases=None,
            deaths=None,
            count_basis=None,
            as_of="2026-10-20",
            quote="On 20 October 2026, Nigeria confirmed an imported case in Abuja.",
        ),
    )
    completer = ScriptedCompleter([draft], [_review("accept")])
    result = await _run(completer)
    assert result.status == "published"
    assert [a["country_code"] for a in result.payload["alerts"]] == ["CD", "KE", "NG"]
    assert result.input_mode == "text"
    # Both models receive the document as an attachment, not pasted into the prompt.
    assert all(call[2] == [ATTACHMENT] for call in completer.calls)


async def test_reviewer_revise_sends_notes_back_and_second_round_publishes():
    bad = _draft(_alert(cases=1))  # 1 is not stated anywhere as a case count for DRC, but is "one death": still cleared below
    good = _draft(_alert())
    completer = ScriptedCompleter(
        [bad, good],
        [_review("revise", issues=["cases for DRC are wrong"]), _review("accept")],
    )
    result = await _run(completer)
    assert result.status == "published"
    assert result.rounds == 2
    second_author_prompt = json.loads(completer.calls[2][1])
    assert second_author_prompt["peer_review_notes"] == ["cases for DRC are wrong"]


async def test_reviewer_reject_is_final():
    completer = ScriptedCompleter([_draft(_alert())], [_review("reject", issues=["not about disease"])])
    result = await _run(completer)
    assert result.status == "rejected"
    assert len(completer.calls) == 2


async def test_event_report_with_no_surviving_alerts_is_not_published():
    hallucinated = _draft(_alert(quote="This sentence is not in the report at all."))
    completer = ScriptedCompleter([hallucinated, hallucinated], [_review("accept"), _review("accept")])
    result = await _run(completer)
    assert result.status == "rejected"


async def test_monitoring_product_with_no_alerts_is_a_valid_empty_reading():
    completer = ScriptedCompleter([_draft(document_type="monitoring_product")], [_review("accept")])
    result = await _run(completer)
    assert result.status == "published"
    assert result.payload["alerts"] == []


async def test_same_model_for_both_roles_is_refused_unless_single_model_is_allowed():
    from src.ai.peer_review import SameModelReviewError

    strict = ScriptedCompleter([_draft(_alert())], [_review("accept")], author_model="m", reviewer_model="m")
    with pytest.raises(SameModelReviewError):
        await _run(strict)

    lenient = ScriptedCompleter([_draft(_alert())], [_review("accept")], author_model="m", reviewer_model="m")
    lenient.single_model = True
    result = await _run(lenient)
    assert result.status == "published"
    assert review_mode(result.author_model, result.reviewer_model) == "single_model"


async def test_unparseable_replies_in_every_round_raise_so_the_report_is_retried():
    with pytest.raises(AIOutputError):
        await _run(ScriptedCompleter(["no json here", '{"alerts": [ {"x": 1'], []))


async def test_a_malformed_author_reply_costs_one_round_not_the_report():
    completer = ScriptedCompleter(["{ truncated", _draft(_alert())], [_review("accept")])
    result = await _run(completer)
    assert result.status == "published"
    assert result.rounds == 2
    assert any("author" in note for note in result.notes)
    retry_prompt = json.loads(completer.calls[1][1])
    assert "not usable" in retry_prompt["peer_review_notes"][0]
