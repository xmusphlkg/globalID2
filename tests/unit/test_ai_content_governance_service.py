from types import SimpleNamespace

from src.domain import TaskStatus, TaskType
from src.services.ai_content_governance_service import (
    AIContentGovernanceService,
    _decisions,
    infer_failed_knowledge_repair_languages,
    is_recoverable_knowledge_failure,
)


def test_infer_failed_knowledge_repair_languages_targets_single_language() -> None:
    assert infer_failed_knowledge_repair_languages("zh: substantive brief is required") == ["zh"]
    assert infer_failed_knowledge_repair_languages("en: profile was not generated") == ["en"]
    assert infer_failed_knowledge_repair_languages("en: timeout | zh: timeout") == []


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
    assert not is_recoverable_knowledge_failure(task)


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
