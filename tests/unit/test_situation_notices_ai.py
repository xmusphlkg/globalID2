"""Offline tests for the peer-reviewed AI flow of the notice-based Situation module."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.services.situation_notices.ai import (
    AIOutputError,
    Completion,
    enrich_notice,
    parse_json_object,
    review_mode,
    sanitize_digest_draft,
    sanitize_notice_draft,
    write_digest,
)
from src.services.situation_notices.pipeline import cluster_notices, content_hash

NOTICE = {
    "notice_id": "who_don:2026-DON700",
    "source_id": "who_don",
    "source_label_en": "WHO Disease Outbreak News",
    "record_type": "outbreak_notice",
    "published_date": "2026-10-20",
    "title_en": "Ebola disease - Kenya",
    "summary_en": "WHO was notified of confirmed Ebola cases in Kenya.",
    "diseases": [{"disease_id": "D050"}],
    "countries": [{"code": "KE"}],
}
DISEASES = [{"disease_id": "D050", "name_en": "Ebola"}, {"disease_id": "D038", "name_en": "Influenza"}]
COUNTRIES = {"KE", "CD", "UG"}

GOOD_DRAFT = {
    "title_zh": "埃博拉病 - 肯尼亚",
    "title_fr": "Maladie à virus Ebola - Kenya",
    "summary_en": "WHO was notified of confirmed Ebola cases in Kenya.",
    "summary_zh": "世卫组织获悉肯尼亚出现埃博拉确诊病例。",
    "summary_fr": "L'OMS a été notifiée de cas confirmés d'Ebola au Kenya.",
    "event_type": "new_outbreak",
    "risk_level": "high",
    "risk_rationale": "Confirmed Ebola cases in a new country.",
    "disease_ids": ["D050"],
    "country_codes": ["KE"],
}


class ScriptedCompleter:
    """Replies from a per-role script and records every call."""

    def __init__(self, author: list, reviewer: list, *, author_model="model-a", reviewer_model="model-b"):
        self.script = {"author": list(author), "reviewer": list(reviewer)}
        self.models = {"author": author_model, "reviewer": reviewer_model}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, role: str, system: str, prompt: str) -> Completion:
        self.calls.append((role, prompt))
        reply = self.script[role].pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return Completion(text=text, model=self.models[role])


def _review(verdict: str, *, issues=None, corrected=None, score=0.9) -> dict:
    return {"verdict": verdict, "score": score, "issues": issues or [], "corrected": corrected}


@pytest.mark.asyncio
async def test_accepted_draft_is_published_after_one_round():
    completer = ScriptedCompleter([GOOD_DRAFT], [_review("accept")])
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "published"
    assert result.rounds == 1
    assert result.author_model == "model-a" and result.reviewer_model == "model-b"
    assert result.payload["risk_level"] == "high"
    assert [role for role, _ in completer.calls] == ["author", "reviewer"]


@pytest.mark.asyncio
async def test_revise_sends_reviewer_notes_back_to_author():
    weak = {**GOOD_DRAFT, "risk_level": "low"}
    completer = ScriptedCompleter(
        [weak, GOOD_DRAFT],
        [_review("revise", issues=["risk understated for a new country"]), _review("accept")],
    )
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "published"
    assert result.rounds == 2
    assert result.payload["risk_level"] == "high"
    second_author_prompt = json.loads(completer.calls[2][1])
    assert second_author_prompt["peer_review_notes"] == ["risk understated for a new country"]


@pytest.mark.asyncio
async def test_final_round_adopts_reviewer_correction_only_if_it_passes_guardrails():
    weak = {**GOOD_DRAFT, "risk_level": "low"}
    completer = ScriptedCompleter(
        [weak, weak],
        [_review("revise", issues=["x"]), _review("revise", issues=["y"], corrected=GOOD_DRAFT)],
    )
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "published"
    assert result.payload["risk_level"] == "high"
    assert "reviewer correction adopted in final round" in result.notes


@pytest.mark.asyncio
async def test_unresolved_revision_is_rejected_not_published():
    weak = {**GOOD_DRAFT, "risk_level": "low"}
    completer = ScriptedCompleter([weak, weak], [_review("revise", issues=["x"]), _review("revise", issues=["y"])])
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_reviewer_reject_is_final():
    completer = ScriptedCompleter([GOOD_DRAFT], [_review("reject", issues=["not an outbreak"])])
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "rejected"
    assert len(completer.calls) == 2


@pytest.mark.asyncio
async def test_same_model_for_both_roles_is_refused():
    completer = ScriptedCompleter([GOOD_DRAFT], [_review("accept")], author_model="m", reviewer_model="m")
    with pytest.raises(AIOutputError):
        await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)


@pytest.mark.asyncio
async def test_single_model_mode_publishes_and_is_labelled():
    completer = ScriptedCompleter([GOOD_DRAFT], [_review("accept")], author_model="m", reviewer_model="m")
    completer.single_model = True
    result = await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)
    assert result.status == "published"
    assert review_mode(result.author_model, result.reviewer_model) == "single_model"
    assert review_mode("a", "b") == "cross_model"


@pytest.mark.asyncio
async def test_unparseable_reply_raises_so_the_notice_is_retried():
    completer = ScriptedCompleter(["no json here"], [])
    with pytest.raises(AIOutputError):
        await enrich_notice(completer, NOTICE, allowed_diseases=DISEASES, allowed_country_codes=COUNTRIES)


def test_guardrails_drop_invented_ids_and_regions():
    draft = {**GOOD_DRAFT, "disease_ids": ["D050", "D999"], "country_codes": ["KE", "EU", "ke"]}
    clean, notes = sanitize_notice_draft(draft, allowed_disease_ids={"D050"}, allowed_country_codes=COUNTRIES)
    assert clean["disease_ids"] == ["D050"]
    assert clean["country_codes"] == ["KE"]
    assert any("D999" in note for note in notes)
    assert any("EU" in note for note in notes)


def test_guardrails_reject_invalid_enums():
    clean, notes = sanitize_notice_draft(
        {**GOOD_DRAFT, "risk_level": "catastrophic", "event_type": "alien"},
        allowed_disease_ids={"D050"},
        allowed_country_codes=COUNTRIES,
    )
    assert clean["risk_level"] is None
    assert clean["event_type"] == "other"
    assert "guardrail: invalid risk_level catastrophic" in notes


def test_parse_json_object_accepts_fenced_reply():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_digest_points_without_valid_citations_are_dropped():
    draft = {
        "headline_en": "Ebola spreads in East Africa",
        "points": [
            {"text_en": "Ebola confirmed in Kenya.", "notice_ids": ["who_don:1"]},
            {"text_en": "Invented claim.", "notice_ids": ["who_don:999"]},
            {"text_en": "Claim with no citation."},
        ],
    }
    clean, notes = sanitize_digest_draft(draft, allowed_notice_ids={"who_don:1"})
    assert [p["text_en"] for p in clean["points"]] == ["Ebola confirmed in Kenya."]
    assert len([n for n in notes if "uncited" in n]) == 2


@pytest.mark.asyncio
async def test_digest_is_published_only_when_points_survive():
    notices = [{"notice_id": "who_don:1", "title_en": "Ebola disease - Kenya"}]
    uncited = {"headline_en": "H", "points": [{"text_en": "x", "notice_ids": ["nope"]}]}
    completer = ScriptedCompleter([uncited, uncited], [_review("accept"), _review("accept")])
    result = await write_digest(completer, "2026-W43", notices)
    assert result.status == "rejected"


def test_content_hash_ignores_fields_the_ai_does_not_read():
    changed = {**NOTICE, "retrieved_at": "2026-10-28T00:00:00Z", "url": "https://example.org/other"}
    assert content_hash(NOTICE) == content_hash(changed)
    assert content_hash(NOTICE) != content_hash({**NOTICE, "title_en": "Ebola disease - Uganda"})


def _row(nid: str, day: date, diseases, countries, risk=None, source="who_don") -> dict:
    return {
        "notice_id": nid,
        "source_id": source,
        "published_date": day,
        "disease_ids": diseases,
        "country_codes": countries,
        "risk_level": risk,
    }


def test_clustering_groups_by_disease_and_place_and_splits_on_gaps():
    rows = [
        _row("a", date(2026, 3, 1), ["D050"], ["KE"], "low"),
        _row("b", date(2026, 10, 1), ["D050"], ["KE"], "moderate"),
        _row("c", date(2026, 10, 20), ["D050"], ["KE"], "high", source="africa_cdc"),
        _row("d", date(2026, 10, 21), ["D050"], [], None),
        _row("e", date(2026, 10, 22), ["D050"], ["KE", "UG"], "moderate"),
    ]
    events = {e["event_id"]: e for e in cluster_notices(rows, as_of=date(2026, 10, 28))}
    assert set(events) == {
        "D050:KE:2026-03-01",
        "D050:KE:2026-10-01",
        "D050:*:2026-10-21",
        "D050:UG:2026-10-22",
    }
    recent = events["D050:KE:2026-10-01"]
    assert recent["notice_ids"] == ["b", "c", "e"]
    assert recent["risk_level"] == "high"
    assert recent["source_ids"] == ["africa_cdc", "who_don"]
    assert recent["status"] == "active"
    assert events["D050:KE:2026-03-01"]["status"] == "quiet"
    assert events["D050:*:2026-10-21"]["country_codes"] == []
