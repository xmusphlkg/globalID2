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
    assert captured["complete"]["wait_for_model_recovery"] is False
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


def test_certified_content_gap_retries_the_model_without_restarting_source_discovery() -> None:
    task = SimpleNamespace(
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
            SimpleNamespace(),
            task,
            "en: missing required sections (epidemiology)",
        )
    )

    assert plan.category == "content_gap"
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


def test_review_all_caps_in_flight_calls_and_keeps_failures_in_place() -> None:
    from src.services import ai_content_governance_service as module

    running = 0
    peak = 0

    async def decide(_completer, item):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        if item == 3:
            raise RuntimeError("model outage")
        return item

    results = asyncio.run(
        AIContentGovernanceService()._review_all(list(range(300)), object(), decide)
    )

    assert peak == module.REVIEW_CONCURRENCY == 192
    assert results[3].args == ("model outage",)
    assert [r for i, r in enumerate(results) if i != 3] == [i for i in range(300) if i != 3]


def test_route_pool_shifts_work_away_from_failing_route_without_starving_it() -> None:
    import random

    from src.ai.peer_review_completer import ReviewRoutePool

    pool = ReviewRoutePool({"p:a": 60, "p:b": 60, "p:c": 60}, line="t", max_tokens=100, rng=random.Random(1))
    for _ in range(50):
        pool.begin("p:c")
        pool.finish("p:c", served_by=None)
        pool.begin("p:a")
        pool.finish("p:a", served_by="p:a")

    assert pool.failure_rate("p:c") == 1.0
    assert pool.weight("p:c") < pool.weight("p:a") / 10
    assert pool.weight("p:c") > 0

    picks = [pool.completer().preferences["author"][0] for _ in range(600)]
    assert picks.count("p:c") < picks.count("p:a") / 5
    reviewers = [pool.completer().preferences["reviewer"][0] for _ in range(600)]
    assert reviewers.count("p:c") < reviewers.count("p:a") / 5
    assert all(
        (c := pool.completer()).preferences["author"][0] != c.preferences["reviewer"][0] for _ in range(200)
    )


def test_concurrent_review_calls_share_one_in_flight_cap() -> None:
    from src.services import ai_content_governance_service as module

    running = 0
    peak = 0

    async def decide(_completer, item):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return item

    async def run():
        service = AIContentGovernanceService()
        return await asyncio.gather(
            *(service._review_all(list(range(150)), object(), decide) for _ in range(4))
        )

    results = asyncio.run(run())

    assert peak == module.REVIEW_CONCURRENCY
    assert all(r == list(range(150)) for r in results)


def test_stream_review_refills_workers_immediately_and_defers_failures(monkeypatch) -> None:
    import contextlib

    import src.core as core
    from src.ai.peer_review import ReviewedOutput
    from src.services import ai_content_governance_service as module

    service = AIContentGovernanceService()
    pending = list(range(200))
    running = 0
    peak = 0
    started_while_slow_running = 0
    slow_running = False
    written: list[int] = []

    @contextlib.asynccontextmanager
    async def fake_db():
        yield object()

    class FakePool:
        def completer(self, avoid=()):
            return object()

    async def fake_route_pool(self):
        return FakePool()

    async def claim(db, kind, limit, claims):
        taken, rest = pending[:limit], pending[limit:]
        pending[:] = rest
        return len(taken), [(i, None, i) for i in taken], 0

    async def decide(model, item):
        nonlocal running, peak, slow_running, started_while_slow_running
        running += 1
        peak = max(peak, running)
        if slow_running:
            started_while_slow_running += 1
        try:
            if item == 0:
                slow_running = True
                await asyncio.sleep(0.3)
                slow_running = False
            else:
                await asyncio.sleep(0.005)
            if item == 7:
                raise RuntimeError("model outage")
            return ReviewedOutput("published", {"decision": "publish"}, "a", "b", "accept", 1, [], 0.9, None)
        finally:
            running -= 1

    async def write(db, kind, results):
        written.extend(key for key, _seen, _reviewed in results)
        return {"published": len(results), "rejected": 0, "stale": 0}

    monkeypatch.setattr(core, "get_db", fake_db)
    monkeypatch.setattr(module.ReviewClaims, "route_pool", fake_route_pool)
    monkeypatch.setattr(service, "_claim_stream_items", claim)
    monkeypatch.setattr(service, "_decide_article", decide)
    monkeypatch.setattr(service, "_write_stream_results", write)

    totals = asyncio.run(service.stream_review("articles", workers=8, fetch_size=16, commit_every=10, commit_seconds=0.05))

    assert peak == 8
    assert totals["claimed"] == 200
    assert totals["deferred"] == 1 and 7 not in written
    assert sorted(written) == [i for i in range(200) if i != 7]
    # While one slow item was still running, the other workers kept pulling new items.
    assert started_while_slow_running > 20


def test_same_model_failure_is_retried_on_other_models_then_gives_up() -> None:
    from src.ai.peer_review import SameModelReviewError
    from src.services import ai_content_governance_service as module

    class Pool:
        def __init__(self):
            self.avoids = []

        def completer(self, avoid=()):
            self.avoids.append(set(avoid))
            return object()

    pool = Pool()
    calls = 0

    async def decide(_model, item):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SameModelReviewError("hy3")
        return item

    assert asyncio.run(module._decide_with_retry(decide, pool, "x")) == "x"
    assert pool.avoids == [set(), {"hy3"}]

    async def always_same(_model, _item):
        raise SameModelReviewError("hy4-preview")

    pool = Pool()
    with pytest.raises(SameModelReviewError):
        asyncio.run(module._decide_with_retry(always_same, pool, "x"))
    assert len(pool.avoids) == module.SAME_MODEL_RETRIES + 1


def test_route_pool_avoids_collapsed_model_while_two_others_remain() -> None:
    import random

    from src.ai.peer_review_completer import ReviewRoutePool

    pool = ReviewRoutePool({"p:a": 10, "p:b": 10, "p:c": 10}, line="t", max_tokens=100, rng=random.Random(3))
    for _ in range(100):
        c = pool.completer({"a"})
        assert "p:a" not in (c.preferences["author"][0], c.preferences["reviewer"][0])

    two = ReviewRoutePool({"p:a": 10, "p:b": 10}, line="t", max_tokens=100, rng=random.Random(3))
    c = two.completer({"a"})
    assert {c.preferences["author"][0], c.preferences["reviewer"][0]} == {"p:a", "p:b"}
