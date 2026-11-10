import asyncio
import json

import pytest
from types import SimpleNamespace

from src.domain import TaskStatus, TaskType
from src.services.ai_content_governance_service import (
    _all_current_briefs_are_published,
    AIContentGovernanceService,
    _call_json_model,
    _decisions,
    infer_failed_knowledge_repair_languages,
    is_recoverable_knowledge_failure,
    plan_failed_knowledge_repair,
)


def test_all_current_briefs_published_requires_at_least_one_published_brief() -> None:
    assert _all_current_briefs_are_published(["published", "published"])
    assert not _all_current_briefs_are_published(["published", "draft"])
    assert not _all_current_briefs_are_published([])


def test_summary_review_scope_skips_unrelated_backlog_and_handles_empty_scope() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.domain import LiteratureArticle, LiteratureSummary

    engine = create_engine("sqlite:///:memory:")
    LiteratureArticle.__table__.create(engine)
    LiteratureSummary.__table__.create(engine)
    with Session(engine) as db:
        for number in range(22):
            article_id = f"article-{number}"
            db.add(LiteratureArticle(article_id=article_id, slug=article_id, title=article_id))
            db.add(LiteratureSummary(article_id=article_id, language="en", status="review", quality_score=1.0))
        db.commit()
        service = AIContentGovernanceService()
        rows = db.execute(service._summary_review_query(None, article_ids=["article-21"]).limit(20)).all()
        assert [article.article_id for _, article in rows] == ["article-21"]
        assert db.execute(service._summary_review_query(None, article_ids=[])).all() == []
        assert len(db.execute(service._summary_review_query(None).limit(20)).all()) == 20


def test_summary_peer_review_requires_current_deterministic_gate(monkeypatch) -> None:
    from src.literature.enrichment import SUMMARY_FIELDS, source_fingerprint
    import src.core

    monkeypatch.setattr(src.core, "get_config", lambda: SimpleNamespace(
        literature=SimpleNamespace(autopilot_summary_min_quality=0.9)
    ))
    article = SimpleNamespace(
        article_id="a", publication_status="published", title="Title", doi="doi",
        journal="Journal", published_at=None, abstract_text="Abstract", integrity_status="current",
    )
    fields = {field: f"Grounded {field}" for field in SUMMARY_FIELDS}
    summary = SimpleNamespace(
        article_id="a", language="en", status="review", generated_by="literature-evidence-agent",
        quality_score=0.95, review_notes="", evidence_map={
            field: {"sources": ["abstract"], "confidence": 0.9} for field in fields
        }, generation_metadata={"source_fingerprint": source_fingerprint(article)}, **fields,
    )
    service = AIContentGovernanceService()
    assert service._summary_precheck(summary, article)[0] == "review"
    summary.quality_score = 0.5
    assert service._summary_precheck(summary, article)[0] == "waiting"
    summary.quality_score = 0.95
    summary.language = "zh"
    summary.generation_metadata.update({
        "protocol_version": 2,
        "bilingual_alignment": {"protocol_version": "canonical-en-translation.v1", "canonical_language": "en", "canonical_summary_fingerprint": "old"},
    })
    assert service._summary_precheck(summary, article, canonical_fingerprint="new")[0] == "waiting"
    assert service._summary_precheck(summary, article, canonical_fingerprint="old")[0] == "review"
    article.publication_status = "excluded"
    assert service._summary_precheck(summary, article, canonical_fingerprint="old")[0] == "waiting"


def test_stream_summary_rechecks_parent_before_applying_review(monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from src.domain import LiteratureArticle, LiteratureSummary
    from src.literature.enrichment import SUMMARY_FIELDS, source_fingerprint
    import src.core

    monkeypatch.setattr(src.core, "get_config", lambda: SimpleNamespace(
        literature=SimpleNamespace(autopilot_summary_min_quality=0.9)
    ))

    async def exercise() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(LiteratureArticle.__table__.create)
            await conn.run_sync(LiteratureSummary.__table__.create)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            article = LiteratureArticle(article_id="a", slug="a", title="Title", publication_status="published")
            db.add(article)
            await db.flush()
            fields = {field: f"Grounded {field}" for field in SUMMARY_FIELDS}
            summary = LiteratureSummary(
                article_id="a", language="en", status="review", generated_by="literature-evidence-agent",
                quality_score=0.95, generation_metadata={"source_fingerprint": source_fingerprint(article)},
                evidence_map={field: {"sources": ["abstract"], "confidence": 0.9} for field in fields},
                **fields,
            )
            db.add(summary)
            await db.commit()
            service = AIContentGovernanceService()
            seen = service._summary_fingerprint(summary)
            article.publication_status = "excluded"
            await db.commit()
            counts = (await service._apply_literature_results(db, "summaries", [
                (summary.id, seen, _reviewed("published", "publish"))
            ])).counts
            assert counts["stale"] == 1
            assert summary.status == "review"
            article.publication_status = "published"
            await db.commit()
            counts = (await service._apply_literature_results(db, "summaries", [
                (summary.id, seen, _reviewed("published", "publish"))
            ])).counts
            assert counts["published"] == 1
            assert summary.status == "published"
        await engine.dispose()

    asyncio.run(exercise())


def test_infer_failed_knowledge_repair_languages_targets_single_language() -> None:
    assert infer_failed_knowledge_repair_languages("zh: substantive brief is required") == ["zh"]
    assert infer_failed_knowledge_repair_languages("en: profile was not generated") == ["en"]
    assert infer_failed_knowledge_repair_languages("en: timeout | zh: timeout") == []


def test_failed_knowledge_repair_plan_extracts_minimal_bilingual_sections() -> None:
    plan = plan_failed_knowledge_repair(
        "D198 generation did not pass the publication gate: "
        "en: status is not published | en: missing required sections (surveillance_note) | "
        "zh: status is not published | zh: missing required sections (risk_groups, surveillance_note)"
    )

    assert plan.category == "content_gap"
    assert plan.languages == ("en", "zh")
    assert plan.sections_by_language == {
        "en": ("surveillance_note",),
        "zh": ("surveillance_note", "risk_groups"),
    }


def test_failed_knowledge_repair_plan_parses_legacy_incomplete_sections_wording() -> None:
    plan = plan_failed_knowledge_repair(
        "en: required sections incomplete (prevention, epidemiology) | "
        "zh: required sections incomplete (clinical_features)"
    )

    assert plan.category == "content_gap"
    assert plan.sections_by_language == {
        "en": ("epidemiology", "prevention"),
        "zh": ("clinical_features",),
    }


def test_failed_knowledge_repair_plan_parses_current_targeted_gate_wording() -> None:
    plan = plan_failed_knowledge_repair(
        "D146 generation did not pass the publication gate: "
        "en: missing repaired target sections (prevention) | "
        "zh: missing repaired target sections (epidemiology, transmission)"
    )

    assert plan.category == "content_gap"
    assert plan.languages == ("en", "zh")
    assert plan.sections_by_language == {
        "en": ("prevention",),
        "zh": ("epidemiology", "transmission"),
    }


def test_governance_model_call_uses_shared_model_center_agent(monkeypatch) -> None:
    captured = {}

    class FakeWorkflowAgent:
        def __init__(self, **kwargs) -> None:
            captured["init"] = kwargs

        async def complete(self, **kwargs) -> str:
            captured["complete"] = kwargs
            return json.dumps({"decisions": [{"id": "42", "decision": "hold"}]})

        def get_latest_conversation(self):
            return {"provider": "qianwen", "model": "qwen-plus"}

    monkeypatch.setattr(
        "src.services.ai_content_governance_service.WorkflowAgent",
        FakeWorkflowAgent,
    )

    payload, route = asyncio.run(
        _call_json_model(
            system="Return JSON only.",
            user_payload={"items": [{"id": "42"}]},
            max_tokens=1200,
        )
    )

    assert payload["decisions"][0]["id"] == "42"
    assert captured["init"]["name"] == "AIContentGovernance"
    assert captured["complete"]["use_cache"] is False
    assert "wait_for_model_recovery" not in captured["complete"]
    assert "max_attempts_per_model" not in captured["complete"]
    assert route == {
        "provider_key": "qianwen",
        "model_name": "qwen-plus",
        "model_key": "qianwen:qwen-plus",
    }


def test_model_failure_plan_does_not_mistake_empty_scaffold_for_content_gap() -> None:
    plan = plan_failed_knowledge_repair(
        "en: generator error: Agent completion failed after trying models ['qwen'] | "
        "en: missing required sections (definition, prevention) | "
        "zh: profile was not generated"
    )

    assert plan.category == "model_transient"
    assert plan.languages == ("en", "zh")
    assert plan.sections_by_language == {}


def test_truncated_model_json_is_a_transient_repair_not_a_content_gap() -> None:
    plan = plan_failed_knowledge_repair(
        "zh: generator error: Unterminated string starting at: line 1 column 184 "
        "| zh: missing required sections (epidemiology)"
    )

    assert plan.category == "model_transient"
    assert plan.languages == ("zh",)
    assert plan.sections_by_language == {}


def test_undifferentiated_draft_status_recomputes_the_localized_profile() -> None:
    plan = plan_failed_knowledge_repair(
        "en: status is not published | zh: status is not published"
    )

    assert plan.category == "publication_status"
    assert plan.languages == ("en", "zh")
    assert plan.sections_by_language == {}


def test_is_recoverable_knowledge_failure_requires_auto_repair_scope() -> None:
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=1,
        max_retries=3,
        input_data={"targeted_repair": True},
        tags=[],
        last_error="KnowledgeEvidenceInsufficientError: zh: substantive brief is required",
    )
    assert is_recoverable_knowledge_failure(task)

    task.last_error = "KnowledgeEvidenceInsufficientError: Agent completion failed after trying models ['qwen3.8-flash']: None"
    assert is_recoverable_knowledge_failure(task)

    task.last_error = "No candidate model available for agent 'knowledge_brief_generator'"
    assert is_recoverable_knowledge_failure(task)

    task.input_data = {}
    task.tags = []
    assert not is_recoverable_knowledge_failure(task)

    task.tags = ["auto_repair"]
    task.retry_count = 3
    assert is_recoverable_knowledge_failure(task)


def test_in_flight_knowledge_failure_can_be_replanned_without_becoming_terminal() -> None:
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.RUNNING,
        retry_count=0,
        max_retries=3,
        input_data={"targeted_repair": True},
        tags=["auto_repair"],
        metadata_={"ai_content_governance_retries": []},
    )
    error = "en: missing required sections (epidemiology)"

    assert not is_recoverable_knowledge_failure(task, error)
    assert is_recoverable_knowledge_failure(task, error, allow_running=True)


def test_repeated_identical_content_gap_is_not_requeued() -> None:
    error = "en: missing required sections (surveillance_note)"
    plan = plan_failed_knowledge_repair(error)
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=1,
        max_retries=5,
        input_data={"targeted_repair": True},
        tags=[],
        metadata_={
            "ai_content_governance_retries": [
                {"category": "content_gap", "failure_fingerprint": plan.fingerprint}
            ]
        },
    )

    assert not is_recoverable_knowledge_failure(task, error)


def test_model_recovery_epoch_allows_one_new_identical_repair_plan() -> None:
    error = "en: missing required sections (epidemiology)"
    plan = plan_failed_knowledge_repair(error)
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=4,
        max_retries=5,
        input_data={"targeted_repair": True, "model_recovery_epoch": "recovery-2"},
        tags=[],
        metadata_={
            "ai_content_governance_retries": [
                {
                    "category": "content_gap",
                    "failure_fingerprint": plan.fingerprint,
                    "model_recovery_epoch": "recovery-1",
                }
            ]
        },
    )

    assert is_recoverable_knowledge_failure(task, error)
    task.metadata_["ai_content_governance_retries"].append(
        {
            "category": "content_gap",
            "failure_fingerprint": plan.fingerprint,
            "model_recovery_epoch": "recovery-2",
        }
    )
    assert not is_recoverable_knowledge_failure(task, error)


def test_first_minimal_content_plan_is_allowed_after_legacy_retry_budget() -> None:
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=5,
        max_retries=5,
        input_data={"targeted_repair": True},
        tags=[],
        metadata_={"ai_content_governance_retries": []},
    )

    assert is_recoverable_knowledge_failure(
        task,
        "en: missing required sections (surveillance_note)",
    )


def test_one_model_center_recovery_is_allowed_after_legacy_retry_budget() -> None:
    error = "en: generator error: Agent completion failed after trying models ['qwen']"
    plan = plan_failed_knowledge_repair(error)
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=5,
        max_retries=5,
        input_data={"targeted_repair": True},
        tags=[],
        metadata_={"ai_content_governance_retries": []},
    )

    assert is_recoverable_knowledge_failure(task, error)
    task.metadata_ = {
        "ai_content_governance_retries": [
            {"category": "model_transient", "failure_fingerprint": plan.fingerprint}
        ]
    }
    assert not is_recoverable_knowledge_failure(task, error)


def test_one_evidence_block_starts_source_first_recovery() -> None:
    error = "D117 source enrichment exhausted: insufficient evidence. No disease brief was generated."
    plan = plan_failed_knowledge_repair(error)
    task = SimpleNamespace(
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        status=TaskStatus.FAILED,
        retry_count=5,
        max_retries=5,
        input_data={"targeted_repair": True},
        tags=[],
        metadata_={"ai_content_governance_retries": []},
    )

    assert plan.category == "evidence_block"
    assert is_recoverable_knowledge_failure(task, error)
    task.metadata_ = {
        "ai_content_governance_retries": [
            {"category": "evidence_block", "failure_fingerprint": plan.fingerprint}
        ]
    }
    assert not is_recoverable_knowledge_failure(task, error)


def test_certified_content_gap_retries_the_model_without_restarting_source_discovery(monkeypatch) -> None:
    from unittest.mock import AsyncMock
    from src.ai.runtime import store

    restart = AsyncMock(return_value=1)
    monkeypatch.setattr(store, "restart_parent_activities", restart)
    db = SimpleNamespace()
    task = SimpleNamespace(
        task_uuid="knowledge-repair",
        task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
        task_name="Repair example knowledge profile",
        description="",
        status=TaskStatus.FAILED,
        retry_count=1,
        max_retries=3,
        input_data={
            "targeted_repair": True,
            "source_refreshed_task_uuid": "source-certificate",
        },
        tags=["knowledge", "auto_repair"],
        metadata_={},
    )

    plan = asyncio.run(
        AIContentGovernanceService()._requeue_knowledge_task(
            db,
            task,
            "en: missing required sections (epidemiology)",
        )
    )

    assert plan.category == "content_gap"
    restart.assert_awaited_once_with(db, task)
    assert task.task_type == TaskType.UPDATE_DISEASE_KNOWLEDGE
    assert task.input_data["source_only"] is False
    assert task.input_data["force"] is False
    assert task.input_data["repair_sections_by_language"] == {"en": ["epidemiology"]}
    assert "publication gate identified" in task.input_data["repair_reasons_by_language"]["en"][0]
    assert "source_refresh" not in task.tags


def test_decisions_sanitizes_unknown_actions_and_confidence() -> None:
    parsed = _decisions(
        {
            "decisions": [
                {"id": "1", "decision": "publish", "confidence": 1.2, "reasons": ["ok"]},
                {"id": "2", "decision": "delete", "confidence": "bad", "reasons": []},
            ]
        },
        {"publish", "hold"},
    )

    assert parsed[0].decision == "publish"
    assert parsed[0].confidence == 1.0
    assert parsed[1].decision == "hold"
    assert parsed[1].confidence == 0.0
    assert parsed[1].reasons == ("No model reason supplied.",)


def test_learning_suggestion_exact_match_maps_without_model() -> None:
    service = AIContentGovernanceService()
    suggestion = SimpleNamespace(id=7, local_name="HIV")
    standards = [
        SimpleNamespace(disease_id="D005", standard_name_en="AIDS", standard_name_zh="艾滋病"),
        SimpleNamespace(disease_id="D900", standard_name_en="HIV", standard_name_zh="人类免疫缺陷病毒感染"),
    ]

    decisions = service._deterministic_learning_decisions([suggestion], standards)

    decision = decisions["7"]
    assert decision.decision == "map"
    assert decision.confidence == 1.0
    assert decision.payload["target_disease_id"] == "D900"


class _ScriptedCompleter:
    """Offline two-model completer: replies are consumed per role in order."""

    single_model = False

    def __init__(self, author: list[dict], reviewer: list[dict]) -> None:
        self.replies = {"author": list(author), "reviewer": list(reviewer)}
        self.calls: list[str] = []

    async def __call__(self, role, system, prompt):
        from src.ai.peer_review import Completion

        self.calls.append(role)
        return Completion(text=json.dumps(self.replies[role].pop(0)), model=f"{role}-model")


def test_peer_decide_publishes_accepted_decision() -> None:
    completer = _ScriptedCompleter(
        author=[{"decision": "publish", "reasons": ["faithful to abstract"]}],
        reviewer=[{"verdict": "accept", "score": 0.9, "issues": []}],
    )
    reviewed = asyncio.run(
        AIContentGovernanceService()._peer_decide(
            completer,
            domain="test",
            author_system="sys",
            task={"item": {"id": "1"}},
            decisions=("publish", "reject"),
            rules=[],
        )
    )
    assert reviewed.published
    assert reviewed.payload["decision"] == "publish"
    assert reviewed.mode == "cross_model"
    assert completer.calls == ["author", "reviewer"]


def test_peer_decide_drops_map_target_outside_catalogue() -> None:
    completer = _ScriptedCompleter(
        author=[
            {"decision": "map", "target_disease_id": "D999", "reasons": ["guess"]},
            {"decision": "map", "target_disease_id": "D001", "reasons": ["exact meaning"]},
        ],
        reviewer=[
            {"verdict": "accept", "score": 0.8, "issues": []},
            {"verdict": "accept", "score": 0.9, "issues": []},
        ],
    )
    reviewed = asyncio.run(
        AIContentGovernanceService()._peer_decide(
            completer,
            domain="test",
            author_system="sys",
            task={"item": {"id": "1"}},
            decisions=("map", "new_concept", "unmapped"),
            rules=[],
            allowed_targets={"D001"},
        )
    )
    # Round 1 is unusable after the guardrail, so the author gets a second round.
    assert reviewed.published
    assert reviewed.payload == {"decision": "map", "reasons": ["exact meaning"], "target_disease_id": "D001"}
    assert any("not in the catalogue" in note for note in reviewed.notes)


def test_peer_decide_reject_is_final_and_not_published() -> None:
    completer = _ScriptedCompleter(
        author=[{"decision": "publish", "reasons": ["looks fine"]}],
        reviewer=[{"verdict": "reject", "score": 0.1, "issues": ["summary invents a case count"]}],
    )
    reviewed = asyncio.run(
        AIContentGovernanceService()._peer_decide(
            completer,
            domain="test",
            author_system="sys",
            task={"item": {"id": "1"}},
            decisions=("publish", "reject"),
            rules=[],
        )
    )
    assert not reviewed.published
    assert reviewed.status == "rejected"


def test_exact_match_learning_suggestion_stores_the_disease_id_string() -> None:
    from src.domain import DiseaseLearningSuggestion, StandardDisease

    suggestion = DiseaseLearningSuggestion(id=7, country_code="DE", local_name="Measles", occurrence_count=3, status="pending")
    standard = StandardDisease(
        disease_id="D021", standard_name_en="Measles", standard_name_zh="麻疹",
        category="test", source="test", metadata_={}, is_active=True,
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class FakeDB:
        calls = 0

        async def execute(self, _statement):
            FakeDB.calls += 1
            return Result([suggestion] if FakeDB.calls == 1 else [standard])

        async def commit(self):
            return None

    result = asyncio.run(
        AIContentGovernanceService().review_learning_suggestions(FakeDB(), use_ai=False)
    )

    assert result["approved"] == 1
    assert result["applied"][0]["target_disease_id"] == "D021"
    assert suggestion.final_disease_id == "D021"
    assert suggestion.status == "approved"


def test_old_hold_without_second_opinion_is_read_again() -> None:
    from src.services.ai_content_governance_service import AIContentGovernanceService as S

    base = {"decision": "hold", "held_fingerprint": "f"}
    assert S._held({"ai_content_governance": base}, "f") is False
    assert S._held({"ai_content_governance": {**base, "second_opinion": True}}, "f") is True
    assert S._held({"ai_content_governance": {**base, "second_opinion": True}}, "g") is False


def _reviewed(status: str, decision: str):
    from src.ai.peer_review import ReviewedOutput

    return ReviewedOutput(status, {"decision": decision, "reasons": ["r"]}, "a", "b", "accept", 1, [], 0.9, None)


def test_batch_reviews_preserve_result_order_and_isolate_item_failures():
    async def decide(_completer, item):
        await asyncio.sleep(0)
        if item == 3:
            raise RuntimeError("model outage")
        return item

    results = asyncio.run(AIContentGovernanceService()._review_all(list(range(12)), object(), decide))
    assert results[3].args == ("model outage",)
    assert [result for index, result in enumerate(results) if index != 3] == [i for i in range(12) if i != 3]


@pytest.mark.parametrize("decisions", [("publish",), ("exclude",), ("approve",), ("map",),
                                      ("new_concept",), ("unmapped",), ("hold", "publish"), ("hold", "hold")])
def test_batch_second_opinion_is_bounded_and_requests_other_models(monkeypatch, decisions):
    from src.services import ai_content_governance_service as module

    requests = []
    def factory(**kwargs):
        requests.append(kwargs)
        return object()
    monkeypatch.setattr(module, "ModelCenterCompleter", factory)
    answers = iter(decisions)
    async def decide(_model, _item):
        return _reviewed("published", next(answers))

    result = asyncio.run(module._review_with_second_opinion(decide, "subject"))
    assert result.payload["decision"] == decisions[-1]
    assert len(requests) == len(decisions)
    assert all(request["allow_single_model"] is False for request in requests)
    if len(decisions) == 2:
        assert requests[1]["avoid_models"] == {"a", "b"}
        assert result.notes[0] == "second opinion completed"


def _article_row(**overrides):
    values = dict(
        article_id="A1", title="t", journal="j", published_at=None, doi=None, pmid=None, authors=[],
        article_type="journal-article", study_type=None, open_access_status="open", relevance_score=1.0,
        public_health_score=0.5, discovery_score=0.6, abstract_text="abstract", source_urls={},
        metadata_={}, publication_status="review", integrity_status="current", peer_review_status="peer_reviewed",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_literature_review_includes_status_and_summary_language() -> None:
    from src.literature.enrichment import SUMMARY_FIELDS

    service = AIContentGovernanceService()
    article = _article_row(peer_review_status="peer_reviewed", integrity_status="current")
    summary = SimpleNamespace(
        language="fr", quality_score=0.9, evidence_map={},
        generation_metadata={}, **{field: "text" for field in SUMMARY_FIELDS},
    )
    article_item = service._literature_article_item(article)
    summary_item = service._literature_review_item("1", summary, article)

    assert article_item["peer_review_status"] == "peer_reviewed"
    assert article_item["integrity_status"] == "current"
    assert summary_item["summary_language"] == "fr"
    assert summary_item["article"]["peer_review_status"] == "peer_reviewed"

    # Existing held fingerprints are invalidated once by the new context;
    # another unchanged hold under this context remains parked.
    old_fingerprint = service._fingerprint({
        "fields": {field: getattr(summary, field) for field in SUMMARY_FIELDS},
        "quality": summary.quality_score,
        "evidence_map": summary.evidence_map,
        "source": None,
    })
    new_fingerprint = service._summary_fingerprint(summary)
    assert old_fingerprint != new_fingerprint
    held = {"ai_content_governance": {
        "decision": "hold", "second_opinion": True, "held_fingerprint": new_fingerprint,
    }}
    assert service._held(held, new_fingerprint)
    summary.language = "zh"
    assert not service._held(held, service._summary_fingerprint(summary))


def test_review_outcome_maps_hold_and_reviewer_disagreement_to_hold() -> None:
    from src.literature.review_application import review_outcome

    assert review_outcome(_reviewed("published", "publish")) == "publish"
    assert review_outcome(_reviewed("published", "exclude")) == "exclude"
    assert review_outcome(_reviewed("published", "reject")) == "reject"
    assert review_outcome(_reviewed("published", "hold")) == "hold"
    # The reviewer never accepted the draft: no verdict on the item, so it holds.
    assert review_outcome(_reviewed("rejected", "exclude")) == "hold"


def test_stream_write_holds_article_in_review_and_skips_it_until_content_changes() -> None:
    service = AIContentGovernanceService()
    row = _article_row()
    seen = service._article_fingerprint(row)

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [row]

    class _DB:
        def add(self, _obj):
            raise AssertionError("a hold must not write a status event")

        async def execute(self, _query):
            return _Result()

    counts = asyncio.run(service._apply_literature_results(_DB(), "articles", [("A1", seen, _reviewed("published", "hold"))])).counts

    assert counts == {"published": 0, "rejected": 0, "held": 1, "stale": 0}
    assert row.publication_status == "review"
    record = row.metadata_["ai_content_governance"]
    assert record["decision"] == "hold"
    assert service._held(row.metadata_, service._article_fingerprint(row))

    row.abstract_text = "a longer abstract with the missing methods"
    assert not service._held(row.metadata_, service._article_fingerprint(row))
