"""Unit tests for the decomposed disease_knowledge helper package and re-export parity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.knowledge import attach_profile_schema
from src.services import disease_knowledge as dk_pkg
from src.services import disease_knowledge_service as service_module
from src.services.disease_knowledge import evidence, repair


def test_service_reexports_all_decomposed_helpers() -> None:
    """Every extracted symbol stays importable from disease_knowledge_service."""
    for name in dk_pkg.__all__:
        assert hasattr(service_module, name), f"Missing re-export on service module: {name}"
        if name == "_knowledge_evidence_limits":
            # Thin wrapper forwards service_module.get_config() for monkeypatch safety.
            assert callable(getattr(service_module, name))
        else:
            assert getattr(service_module, name) is getattr(dk_pkg, name), (
                f"Re-export identity mismatch for {name}"
            )


def test_default_disease_csv_points_to_repo_config() -> None:
    assert evidence.DEFAULT_DISEASE_CSV == service_module.DEFAULT_DISEASE_CSV
    assert evidence.DEFAULT_DISEASE_CSV.name == "standard_diseases.csv"
    assert evidence.DEFAULT_DISEASE_CSV.parent.name == "configs"
    assert evidence.DEFAULT_DISEASE_CSV.exists()


def test_knowledge_evidence_limits_respects_service_get_config_monkeypatch(monkeypatch) -> None:
    fake_config = SimpleNamespace(
        ai=SimpleNamespace(
            knowledge_evidence_max_sources=7,
            knowledge_evidence_manifest_max_characters=9500,
        )
    )
    monkeypatch.setattr(service_module, "get_config", lambda: fake_config)
    assert service_module._knowledge_evidence_limits() == {
        "max_sources": 7,
        "max_manifest_characters": 9500,
    }
    assert evidence._knowledge_evidence_limits(fake_config) == {
        "max_sources": 7,
        "max_manifest_characters": 9500,
    }


def test_evidence_helpers_aliases_expansion_and_refresh_ttl() -> None:
    aliases = evidence._evidence_entity_aliases(
        {
            "name_en": "  Measles ",
            "standard_name_en": "measles",
            "name_zh": "麻疹",
            "query_aliases": ["Rubeola", "MEASLES", ""],
        }
    )
    assert aliases == ["Measles", "麻疹", "Rubeola"]

    assert evidence.expand_sources(["who", "search", "who_don"]) == [
        "who",
        "who_don",
        "web_search",
    ]
    assert "msd" not in evidence.expand_sources(None)

    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    fresh_source = {
        "status": "active",
        "review_status": "approved",
        "fetched_at": (now - timedelta(days=5)).isoformat(),
    }
    stale_source = {
        "status": "active",
        "review_status": "approved",
        "fetched_at": (now - timedelta(days=45)).isoformat(),
    }
    assert not evidence._sources_need_refresh([fresh_source], now=now)
    assert evidence._sources_need_refresh([stale_source], now=now)
    assert evidence._sources_need_refresh([], now=now)


def test_evidence_attribution_discovery_state_and_retry_backoff() -> None:
    sources_by_id = {
        42: {
            "id": 42,
            "source_name": "WHO Fact Sheet",
            "source_type": "who",
            "title": "Measles",
            "url": "https://www.who.int/measles",
            "resolved_url": "https://www.who.int/measles",
            "license": "CC-BY",
            "fetched_at": "2026-11-20T00:00:00+00:00",
            "metadata": {"publisher": "WHO", "year": 2026},
        }
    }
    enriched = evidence._enrich_source_attribution(
        [{"source_id": 42, "citation_index": 1}],
        sources_by_id,
    )
    assert len(enriched) == 1
    assert enriched[0]["source_name"] == "WHO Fact Sheet"
    assert enriched[0]["publisher"] == "WHO"
    assert enriched[0]["year"] == 2026

    transport_gap = {
        "source_gap": True,
        "adapter_outcomes": {"who": "timeout", "pubmed": "cooldown"},
    }
    assert evidence._source_discovery_state(transport_gap) == "awaiting_source_transport"
    assert not evidence._source_gap_blocks_publication(transport_gap)

    evidence_gap = {
        "source_gap": True,
        "adapter_outcomes": {"who": "empty", "pubmed": "timeout"},
    }
    assert evidence._source_discovery_state(evidence_gap) == "awaiting_evidence"
    assert evidence._source_gap_blocks_publication(evidence_gap)

    assert (
        evidence._source_transport_retry_delay_seconds(
            attempt=1,
            initial_delay_seconds=60,
            maximum_delay_seconds=600,
        )
        == 60
    )
    assert (
        evidence._source_transport_retry_delay_seconds(
            attempt=5,
            initial_delay_seconds=60,
            maximum_delay_seconds=600,
        )
        == 600
    )


def test_repair_helpers_citation_markers_route_failure_and_candidate_selection() -> None:
    rewritten = repair._use_source_id_citation_markers(
        {
            "brief": "Overview backed by evidence [1] and [2].",
            "source_attribution": [
                {"citation_index": 1, "source_id": 101},
                {"citation_index": 2, "source_id": 202},
            ],
        }
    )
    assert rewritten["brief"] == "Overview backed by evidence [101] and [202]."

    assert repair._model_center_route_failure(
        {"trace": {"error": "All models failed due to rate limit"}}
    )
    assert not repair._model_center_route_failure({"trace": {"error": "schema mismatch"}})

    disease = attach_profile_schema(
        {
            "disease_id": "D001",
            "name_en": "Measles",
            "name_zh": "麻疹",
            "category": "Viral",
        }
    )
    skipped = repair._skipped_language_after_route_failure(
        disease=disease,
        language="zh",
        target_sections=["brief", "definition"],
        upstream_language="en",
    )
    assert skipped["payload"]["language"] == "zh"
    assert skipped["payload"]["status"] == "draft"
    assert skipped["payload"]["metadata"]["automation_state"] == "waiting_for_model_capacity"
    assert skipped["trace"]["generator"] == "skipped_after_model_route_failure"

    assert repair._normalize_generator_mode("AUTO") == "auto"
    assert repair._normalize_generator_mode("unknown") == "ai"
    assert repair._total_tokens_from_usage({"total": 1234.0}) == 1234
    assert repair._total_tokens_from_usage(None) is None

    active_task = SimpleNamespace(
        task_uuid="task-1",
        status="running",
        input_data={"disease_id": "d002"},
    )
    active_map = repair._active_knowledge_tasks_by_disease([active_task])
    assert "D002" in active_map

    selected, skipped_candidates = repair._select_knowledge_repair_candidates(
        [
            {
                "disease_id": "D002",
                "repair_priority": "urgent",
                "repair_sections": ["brief"],
                "repair_reasons_by_language": {"en": ["missing_language_profile"]},
            },
            {
                "disease_id": "D001",
                "repair_priority": "high",
                "repair_sections": ["definition"],
                "repair_reasons_by_language": {"en": ["content_gap"]},
            },
        ],
        active_by_disease=active_map,
    )
    assert [row["disease_id"] for row in selected] == ["D001"]
    assert skipped_candidates == [
        {
            "disease_id": "D002",
            "reason": "already_running",
            "existing_task_uuid": "task-1",
            "existing_status": "running",
        }
    ]
