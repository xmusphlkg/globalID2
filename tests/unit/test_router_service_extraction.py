"""Unit tests for extracted router service modules and router re-export parity."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dashboard.api.routers import ai as ai_router
from dashboard.api.routers import sources as sources_router
from dashboard.api.schemas.ai_tasks import AIStartRequest
from dashboard.api.schemas.disease_knowledge import DiseaseKnowledgeStartRequest
from dashboard.api.schemas.model_center import (
    ModelCreateRequest,
    ModelUpdateRequest,
    ProviderUpdateRequest,
)
from dashboard.api.services import ai_model_center_service as ai_service
from dashboard.api.services import sources_flow_service as sources_service
from src.domain.task import TaskPriority


# ---------------------------------------------------------------------------
# Re-export parity checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "DATA_PIPELINE_STAGES",
        "_ICELAND_HISTORY_SCOPES",
        "_SERIES_SOURCE_SCOPE_OVERRIDES",
        "_OBSERVATION_GEOGRAPHY_SERIES_SOURCES",
        "_source_channel_for_panel",
        "_source_kind",
        "_source_supports_start_year",
        "_series_source_scope",
        "_increment_count",
        "_validate_country_automation_options",
        "_contains_chinese",
        "_country_name_zh",
        "_history_start_year",
        "_optional_bool",
        "_source_policy_config",
        "_build_source_policy",
        "_country_source_config",
        "_task_status",
        "_should_show_task_only_source",
        "_task_country_code",
        "_scope_from_task",
        "_source_from_task",
        "_pick_latest_crawl_task",
        "_segment_progress",
        "_build_data_stages",
        "_validate_automation_schedule",
        "_automation_job_out",
        "_load_series_flow_facts",
        "_observation_rollup",
    ],
)
def test_sources_router_reexport_parity(symbol: str) -> None:
    assert getattr(sources_router, symbol) is getattr(sources_service, symbol)


@pytest.mark.parametrize(
    "symbol",
    [
        "_without_completion_caps",
        "_apply_availability",
        "_normalize_report_type",
        "_normalize_priority",
        "_normalize_reuse_strategy",
        "_normalize_report_layout",
        "_normalize_analysis_depth",
        "_normalize_source_groups",
        "_dedupe_disease_ids",
        "_load_active_knowledge_tasks",
        "_active_knowledge_tasks_by_disease",
        "_find_disease_in_catalogue",
        "_task_to_out",
        "_provider_to_out",
        "_model_to_out",
    ],
)
def test_ai_router_reexport_parity(symbol: str) -> None:
    assert getattr(ai_router, symbol) is getattr(ai_service, symbol)


@pytest.mark.parametrize(
    "schema_name",
    [
        "AIStartRequest",
        "AIContentGovernanceRequest",
        "AIContentGovernanceResponse",
        "KnowledgeRepairRetryRequest",
        "DiseaseDuplicateAuditRequest",
        "DiseaseDuplicateAuditStatusRequest",
        "DiseaseKnowledgeStartRequest",
        "DiseaseKnowledgeDetail",
        "ProviderCreateRequest",
        "ProviderUpdateRequest",
        "ProviderOut",
        "ModelCreateRequest",
        "ModelUpdateRequest",
        "ModelOut",
        "RuntimeRouteOut",
        "AIModelCallLogOut",
    ],
)
def test_ai_router_schema_reexports_present(schema_name: str) -> None:
    assert hasattr(ai_router, schema_name)


# ---------------------------------------------------------------------------
# sources_flow_service pure helper unit tests
# ---------------------------------------------------------------------------


def test_sources_service_scope_resolution_and_start_year_rules() -> None:
    assert (
        sources_service._series_source_scope("SRC_FR_ECDC_ATLAS", country_code="FR")
        == "ecdc_atlas_annual"
    )
    assert (
        sources_service._series_source_scope("SRC_IS_DOH_HISTORY", country_code="IS")
        == "is_doh_history"
    )
    assert sources_service._source_kind("IS", "is_doh_history") == "history"
    assert sources_service._source_kind("TH", "th_doe506_monthly") == "history"
    assert sources_service._source_kind("IS", "is_doh_annual") == "current"

    assert (
        sources_service._source_supports_start_year(
            "IS", "is_doh_history", configured_start_year=1997
        )
        is False
    )
    assert (
        sources_service._source_supports_start_year(
            "TH", "th_dds_weekly", configured_start_year=2020
        )
        is False
    )
    assert (
        sources_service._source_supports_start_year(
            "MY", "my_idengue_daily", configured_start_year=2020
        )
        is False
    )
    assert (
        sources_service._source_supports_start_year(
            "IS", "is_doh_annual", configured_start_year=2010
        )
        is True
    )
    assert (
        sources_service._source_supports_start_year(
            "IS", "is_doh_annual", configured_start_year=None
        )
        is False
    )


def test_sources_service_stage_assembly_and_progress_segments() -> None:
    assert sources_service._segment_progress(15, 0, 30) == 50
    assert sources_service._segment_progress(0, 30, 50) == 0
    assert sources_service._segment_progress(95, 50, 90) == 100

    empty_stages = sources_service._build_data_stages(None, [])
    assert [s.stage for s in empty_stages] == sources_service.DATA_PIPELINE_STAGES
    assert all(s.status is None and s.progress == 0 for s in empty_stages)

    when = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    completed_task = SimpleNamespace(
        task_uuid="task-1",
        task_name="Crawl US",
        status="completed",
        progress=100,
        created_at=when,
        completed_at=when,
    )
    workbook = [
        SimpleNamespace(title="Crawl Completed", content="No new data found in window")
    ]
    stages = sources_service._build_data_stages(completed_task, workbook)
    by_stage = {s.stage: s for s in stages}
    assert by_stage["fetch_list"].status == "completed"
    assert by_stage["incremental_check"].status == "completed"
    assert by_stage["process_store"].status == "skipped"
    assert by_stage["process_store"].task_name == "Skipped (No New Data)"
    assert by_stage["finalize"].status == "completed"


def test_sources_service_build_policy_and_schedule_validation() -> None:
    policy = sources_service._build_source_policy(
        {"public_release_enabled": True},
        {
            "temporal_granularity": "weekly",
            "supports_current_month": True,
            "default_include_current_month": True,
            "refresh_recent_weeks": 99,
            "dynamic_revision_enabled": True,
        },
    )
    assert policy.temporal_granularity == "weekly"
    assert policy.revision_window_unit == "weeks"
    assert policy.default_revision_window == 52
    assert policy.default_include_current_month is True

    sources_service._validate_automation_schedule(60, None)
    sources_service._validate_automation_schedule(None, "06:30")
    with pytest.raises(HTTPException):
        sources_service._validate_automation_schedule(None, None)
    with pytest.raises(HTTPException):
        sources_service._validate_automation_schedule(0, None)
    with pytest.raises(HTTPException):
        sources_service._validate_automation_schedule(None, "25:00")


def test_sources_service_flow_assembly_merges_and_filters() -> None:
    when = datetime(2026, 5, 1, tzinfo=timezone.utc)
    src_rows = [
        SimpleNamespace(
            country_id=10,
            country_code="CN",
            country_name="China",
            data_source="China CDC Weekly",
            record_count=5,
            earliest_date=when,
            latest_date=when,
        )
    ]
    series_rows = [
        SimpleNamespace(
            country_id=10,
            country_code="CN",
            country_name="China",
            source_system="SRC_CN_CDC",
            series_count=3,
            observation_count=12,
            earliest_date=when,
            latest_date=when,
        )
    ]
    definition_rows = [
        SimpleNamespace(
            country_id=10,
            country_code="CN",
            source_system="SRC_CN_CDC",
            availability_status="active",
            metric_type="case_notifications",
            mapping_relation="exact",
            comparability="comparable",
            count=3,
        )
    ]
    quality_rows = [
        SimpleNamespace(
            country_id=10,
            country_code="CN",
            source_system="SRC_CN_CDC",
            quality_status="accepted",
            count=12,
        )
    ]
    availability_rows = [
        SimpleNamespace(
            country_id=10,
            country_code="CN",
            source_system="SRC_CN_CDC",
            status="available",
            count=3,
        )
    ]
    counts = sources_service.index_series_fact_counts(
        definition_rows, quality_rows, availability_rows
    )
    merged = sources_service.merge_record_and_series_sources(
        src_rows, series_rows, counts
    )
    flows = sources_service.assemble_data_source_flows(merged, {}, {})
    assert len(flows) == 1
    assert flows[0].source_scope == "cdc_weekly"
    assert flows[0].record_count == 5
    assert flows[0].source_series_count == 3
    assert flows[0].source_observation_count == 12
    assert flows[0].observation_quality == {"accepted": 12}


# ---------------------------------------------------------------------------
# ai_model_center_service pure helper unit tests
# ---------------------------------------------------------------------------


def test_ai_service_normalizers_and_completion_caps() -> None:
    cleaned = ai_service._without_completion_caps(
        {
            "max_tokens": 1000,
            "max_completion_tokens": 500,
            "runtime_completion_max_tokens": 200,
            "health_check_max_tokens": 32,
            "top_p": 0.9,
        }
    )
    assert cleaned == {"top_p": 0.9}

    assert ai_service._normalize_report_type("MONTHLY") == "monthly"
    assert ai_service._normalize_priority("high") == TaskPriority.HIGH
    assert ai_service._normalize_reuse_strategy("safe") == "safe"
    assert ai_service._normalize_report_layout("structured") == "structured"
    assert ai_service._normalize_analysis_depth("deterministic") == "deterministic"
    assert ai_service._dedupe_disease_ids([" d001 ", "D001", "", "d002"]) == [
        "D001",
        "D002",
    ]

    with pytest.raises(HTTPException) as exc:
        ai_service._normalize_priority("not_a_priority")
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        ai_service._normalize_source_groups(["not_a_real_source"])
    assert exc.value.status_code == 422


def test_ai_service_task_specs_and_active_indexing() -> None:
    k_body = DiseaseKnowledgeStartRequest(disease_ids=["D001", "D002"], task_name="Batch")
    k_spec = ai_service.build_knowledge_task_spec(
        k_body,
        disease={"disease_id": "D001", "name_en": "Influenza"},
        disease_id="D001",
        requested_count=2,
        source_groups=["who", "pubmed"],
    )
    assert k_spec["task_name"] == "Batch · Influenza"
    assert k_spec["input_data"]["disease_id"] == "D001"
    assert k_spec["workbook_metadata"]["source_groups"] == ["who", "pubmed"]

    r_body = AIStartRequest(country_code="US", language="en")
    r_spec = ai_service.build_report_task_spec(
        r_body,
        country_code="US",
        report_type="monthly",
        report_layout="analytical_v3",
        analysis_depth="deep",
        reuse_strategy="auto",
        reuse_report_id=None,
    )
    assert r_spec["task_name"] == "Generate MONTHLY Report for US"
    assert r_spec["input_data"]["language"] == "en"

    bad_lang_body = AIStartRequest(country_code="US", language="fr")
    with pytest.raises(HTTPException) as exc:
        ai_service.build_report_task_spec(
            bad_lang_body,
            country_code="US",
            report_type="monthly",
            report_layout="analytical_v3",
            analysis_depth="deep",
            reuse_strategy="auto",
            reuse_report_id=None,
        )
    assert exc.value.status_code == 422

    t1 = SimpleNamespace(task_uuid="u1", input_data={"disease_ids": ["d001", "d002"]})
    t2 = SimpleNamespace(task_uuid="u2", input_data={"disease_id": "d002"})
    indexed = ai_service._active_knowledge_tasks_by_disease([t1, t2])
    assert indexed["D001"] is t1
    assert indexed["D002"] is t1


def test_ai_service_provider_and_model_mutation_and_projection() -> None:
    provider = SimpleNamespace(
        id=1,
        provider_key="openai",
        provider_name="OpenAI",
        display_name="OpenAI",
        api_style="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret-1234567890",
        organization=None,
        is_active=True,
        priority=10,
        extra_headers={},
        extra_config={},
        last_check_status="ok",
        last_check_message=None,
        last_checked_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    ai_service.apply_provider_updates(
        provider,
        ProviderUpdateRequest(display_name="OpenAI Prod", clear_api_key=True, priority=5),
    )
    assert provider.display_name == "OpenAI Prod"
    assert provider.api_key is None
    assert provider.priority == 5

    provider_out = ai_service._provider_to_out(provider)
    assert provider_out.provider_key == "openai"
    assert provider_out.has_api_key is False

    create_req = ModelCreateRequest(
        provider_key="openai",
        model_name="gpt-4o",
        runtime_max_concurrency=4,
        thinking_mode="disabled",
        extra_params={"max_tokens": 999, "seed": 7},
    )
    extra = ai_service.build_new_model_extra_params(create_req)
    assert "max_tokens" not in extra
    assert extra["seed"] == 7
    assert extra["runtime_max_concurrency"] == 4

    model = SimpleNamespace(
        id=2,
        provider_id=1,
        provider=provider,
        model_key="openai:gpt-4o",
        model_name="gpt-4o",
        display_name="GPT-4o",
        model_type="chat",
        api_style="openai",
        temperature=0.2,
        max_tokens=100,
        extra_params=extra,
        is_enabled=True,
        is_default=True,
        priority=10,
        last_check_status="ok",
        last_check_message=None,
        last_checked_at=None,
    )
    ai_service.apply_model_updates(
        model,
        ModelUpdateRequest(display_name="GPT-4o Fast", runtime_max_concurrency=None),
    )
    assert model.display_name == "GPT-4o Fast"
    assert model.max_tokens is None
    assert "runtime_max_concurrency" not in model.extra_params

    model_out = ai_service._model_to_out(model)
    assert model_out.model_key == "openai:gpt-4o"
    assert model_out.provider_key == "openai"
    assert model_out.runtime_max_concurrency is None


def test_ai_service_call_log_and_runtime_route_serialization() -> None:
    when = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
    log_row = SimpleNamespace(
        id=99,
        request_id="req-1",
        model_key="openai:gpt-4o",
        model_name="gpt-4o",
        provider_key="openai",
        agent_name="reviewer",
        status="success",
        latency_ms=420,
        error_type=None,
        error_message=None,
        created_at=when,
    )
    log_out = ai_service.call_log_to_out(log_row)
    assert log_out.id == 99
    assert log_out.created_at == when.isoformat()

    query = ai_service.build_call_logs_query(
        provider_key="openai", model_key="openai:gpt-4o", status="success"
    )
    paged_sql = str(
        ai_service.order_and_paginate_call_logs_query(query, page=2, page_size=25)
    )
    assert "LIMIT" in paged_sql and "OFFSET" in paged_sql

    route_out = ai_service.runtime_route_to_out(
        {
            "model_id": 2,
            "model_key": "openai:gpt-4o",
            "model_name": "gpt-4o",
            "provider_id": 1,
            "provider_key": "openai",
            "provider_name": "OpenAI",
            "api_style": "openai",
            "api_key": "sk-1234567890abcdef",
            "available_for_routing": True,
            "routing_status": "ready",
            "runtime_model_capacity": 4,
            "runtime_model_inflight": 1,
        }
    )
    assert route_out.has_api_key is True
    assert route_out.routing_status == "ready"
    assert route_out.runtime_model_capacity == 4
    assert route_out.runtime_model_inflight == 1
