import asyncio
import json
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
