from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services.knowledge_automation_service import (
    _source_discovery_exhausted,
    _source_transport_retry_context,
    _task_repair_priority,
    knowledge_backlog_slots,
)


def test_knowledge_backlog_slots_are_bounded() -> None:
    assert knowledge_backlog_slots(0, target=12, batch_size=6) == 6
    assert knowledge_backlog_slots(9, target=12, batch_size=6) == 3
    assert knowledge_backlog_slots(12, target=12, batch_size=6) == 0
    assert knowledge_backlog_slots(20, target=12, batch_size=6) == 0


def test_source_backlog_slots_are_independent_from_model_queue_depth() -> None:
    # The scheduler supplies source-task depth, not all knowledge tasks, so
    # model-provider cooldowns cannot stop source discovery from progressing.
    assert knowledge_backlog_slots(0, target=12, batch_size=6) == 6


def test_normal_revalidation_stops_when_model_backlog_is_full() -> None:
    # Source-first repairs continue on their independent lane, while routine
    # policy revalidation must not add more work to a full model queue.
    assert knowledge_backlog_slots(48, target=12, batch_size=6) == 0


def test_canonical_content_gap_priority_overrides_stale_task_priority() -> None:
    task = SimpleNamespace(
        input_data={"disease_id": "D100", "repair_priority": "normal"},
    )

    assert _task_repair_priority(
        task,
        canonical_priorities={"D100": "high"},
    ) == "high"

    task.input_data["repair_priority"] = "high"
    assert _task_repair_priority(
        task,
        canonical_priorities={"D100": "normal"},
    ) == "normal"


def test_evidence_backoff_is_invalidated_by_a_policy_upgrade() -> None:
    task = SimpleNamespace(
        output_data={
            "source_discovery_exhausted": True,
            "evidence_policy_version": 3,
            "source_strategy_version": 1,
        },
        metadata_={"knowledge_automation_state": "awaiting_evidence"},
    )

    assert not _source_discovery_exhausted(
        task,
        evidence_policy_version=4,
        source_strategy_version=1,
    )
    assert _source_discovery_exhausted(
        task,
        evidence_policy_version=3,
        source_strategy_version=1,
    )
    assert not _source_discovery_exhausted(
        task,
        evidence_policy_version=3,
        source_strategy_version=2,
    )


def test_source_backoff_is_invalidated_by_a_profile_contract_change() -> None:
    task = SimpleNamespace(
        output_data={
            "source_discovery_exhausted": True,
            "evidence_policy_version": 4,
            "source_strategy_version": 7,
            "profile_schema_signature": "old-contract",
        },
        metadata_={},
    )

    assert not _source_discovery_exhausted(
        task,
        evidence_policy_version=4,
        source_strategy_version=7,
        profile_schema_signature="new-contract",
    )
    assert _source_discovery_exhausted(
        task,
        evidence_policy_version=4,
        source_strategy_version=7,
        profile_schema_signature="old-contract",
    )


def test_source_transport_backoff_preserves_retry_context_without_evidence_cooldown() -> None:
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        output_data={
            "source_discovery_state": "awaiting_source_transport",
            "source_retry_after": (now + timedelta(minutes=5)).isoformat(),
            "source_transport_attempt": 2,
            "evidence_policy_version": 4,
            "source_strategy_version": 4,
        },
        metadata_={},
    )

    deferred, context = _source_transport_retry_context(
        task,
        now=now,
        evidence_policy_version=4,
        source_strategy_version=4,
    )
    assert deferred
    assert context == {"source_transport_attempt": 2}
    assert not _source_discovery_exhausted(
        task,
        evidence_policy_version=4,
        source_strategy_version=4,
    )

    task.output_data["source_retry_after"] = (now - timedelta(seconds=1)).isoformat()
    deferred, context = _source_transport_retry_context(
        task,
        now=now,
        evidence_policy_version=4,
        source_strategy_version=4,
    )
    assert not deferred
    assert context == {"source_transport_attempt": 2}
