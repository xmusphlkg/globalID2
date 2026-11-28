"""Cohesive model-center serialization, health projection, and task-helper service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai.catalogue.health import (
    get_model_rate_limit_state,
    get_model_runtime_health_state,
    get_provider_rate_limit_state,
    get_provider_runtime_health_state,
)
from src.ai.catalogue.options import (
    availability_view,
    get_thinking_mode,
    with_availability,
    with_thinking_mode,
)
from src.ai.model_center import mask_api_key
from src.core.db_schema import ensure_task_type_enum_schema
from src.domain.ai_model_center import AIModelCallLog, AIModelConfig, AIProviderConfig
from src.domain.country import Country
from src.domain.report import Report, ReportType
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType
from src.services.disease_knowledge_service import (
    SOURCE_GROUPS,
    DiseaseKnowledgeUpdateService,
    expand_sources,
)

from ..schemas.ai_tasks import AIStartRequest
from ..schemas.disease_knowledge import (
    DiseaseKnowledgeRepairRequest,
    DiseaseKnowledgeRepairResponse,
    DiseaseKnowledgeStartRequest,
    DiseaseKnowledgeStartResponse,
    DiseaseKnowledgeTaskSkipped,
)
from ..schemas.model_center import (
    AIModelCallLogOut,
    ModelCreateRequest,
    ModelOut,
    ModelUpdateRequest,
    ProviderCreateRequest,
    ProviderOut,
    ProviderUpdateRequest,
    RuntimeRouteOut,
)
from ..schemas.task import TaskOut


def _without_completion_caps(extra_params: Any) -> Dict[str, Any]:
    """Drop stored completion budgets so they cannot be forwarded upstream."""
    params = dict(extra_params) if isinstance(extra_params, dict) else {}
    for key in (
        "max_tokens",
        "max_completion_tokens",
        "runtime_completion_max_tokens",
        "health_check_max_tokens",
    ):
        params.pop(key, None)
    return params


def _apply_availability(
    extra_params: Dict[str, Any], timezone_name: Optional[str], windows: Any
) -> Dict[str, Any]:
    try:
        return with_availability(extra_params, timezone_name, windows)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _normalize_report_type(value: str) -> str:
    normalized = (value or "monthly").strip().lower()
    allowed = {rt.value for rt in ReportType}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(422, f"Invalid report_type '{value}'. Allowed values: {joined}")
    return normalized


def _normalize_priority(value: str) -> TaskPriority:
    normalized = (value or TaskPriority.NORMAL.value).strip().lower()
    try:
        return TaskPriority(normalized)
    except ValueError as exc:
        allowed = ", ".join([p.value for p in TaskPriority])
        raise HTTPException(422, f"Invalid priority '{value}'. Allowed values: {allowed}") from exc


def _normalize_reuse_strategy(value: str) -> str:
    normalized = (value or "auto").strip().lower()
    allowed = {"auto", "safe", "resume", "manual"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(
            422, f"Invalid reuse_strategy '{value}'. Allowed values: {joined}"
        )
    return normalized


def _normalize_report_layout(value: str) -> str:
    normalized = (value or "analytical_v3").strip().lower()
    allowed = {"analytical_v3", "structured", "legacy"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(
            422, f"Invalid report_layout '{value}'. Allowed values: {joined}"
        )
    return normalized


def _normalize_analysis_depth(value: str) -> str:
    normalized = (value or "deep").strip().lower()
    allowed = {"deep", "deterministic"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(
            422, f"Invalid analysis_depth '{value}'. Allowed values: {joined}"
        )
    return normalized


def _normalize_source_groups(values: list[str] | None) -> list[str]:
    cleaned = [
        str(value).strip().lower() for value in (values or []) if str(value).strip()
    ]
    if not cleaned:
        cleaned = ["who", "search", "wikidata", "wikipedia", "pubmed", "msd"]
    allowed = set(SOURCE_GROUPS)
    invalid = sorted({value for value in cleaned if value not in allowed})
    if invalid:
        joined = ", ".join(invalid)
        raise HTTPException(422, f"Invalid source groups: {joined}")
    return expand_sources(cleaned)


def _dedupe_disease_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        disease_id = str(value or "").strip().upper()
        if not disease_id or disease_id in seen:
            continue
        seen.add(disease_id)
        result.append(disease_id)
    return result


async def _load_active_knowledge_tasks(db: AsyncSession) -> list[Task]:
    result = await db.execute(
        select(Task)
        .where(
            Task.task_type == TaskType.UPDATE_DISEASE_KNOWLEDGE,
            Task.status.in_(
                [
                    TaskStatus.PENDING,
                    TaskStatus.QUEUED,
                    TaskStatus.RUNNING,
                    TaskStatus.RETRYING,
                ]
            ),
        )
        .order_by(Task.created_at.asc())
    )
    return list(result.scalars().all())


def _active_knowledge_tasks_by_disease(tasks: list[Task]) -> dict[str, Task]:
    result: dict[str, Task] = {}
    for task in tasks:
        input_data = dict(task.input_data or {})
        disease_ids = input_data.get("disease_ids")
        disease_id = input_data.get("disease_id")
        candidates: list[str] = []
        if isinstance(disease_ids, list):
            candidates.extend(
                str(value).strip().upper()
                for value in disease_ids
                if str(value).strip()
            )
        if disease_id:
            candidates.append(str(disease_id).strip().upper())
        for candidate in candidates:
            if candidate and candidate not in result:
                result[candidate] = task
    return result


def _find_disease_in_catalogue(
    service: DiseaseKnowledgeUpdateService, disease_id: str
) -> dict[str, Any] | None:
    wanted = str(disease_id or "").strip().upper()
    for disease in service.load_standard_diseases():
        if str(disease.get("disease_id") or "").upper() == wanted:
            return disease
    return None


def build_knowledge_task_spec(
    body: DiseaseKnowledgeStartRequest,
    *,
    disease: dict[str, Any],
    disease_id: str,
    requested_count: int,
    source_groups: list[str],
) -> Dict[str, Any]:
    """Build task metadata, input_data, and initial workbook payload for a knowledge run."""
    task_name = body.task_name or f"Update {disease['name_en']} knowledge"
    if body.task_name and requested_count > 1:
        task_name = f"{body.task_name} · {disease['name_en']}"

    description = body.description or (
        f"Disease: {disease_id}, "
        f"Sources: {', '.join(source_groups) or 'default'}, "
        f"Force: {'Yes' if body.force else 'No'}, "
        f"Generator: {body.generator}"
    )

    return {
        "task_name": task_name,
        "description": description,
        "input_data": {
            "disease_id": disease_id,
            "disease_ids": [disease_id],
            "source_groups": source_groups,
            "source": source_groups,
            "force": body.force,
            "generator": body.generator,
            "initiated_via": "dashboard",
            "requested_by": "ai-control-panel",
        },
        "workbook_content": (
            f"Queued from AI control panel for disease {disease_id} "
            f"with sources {', '.join(source_groups) or 'default'}."
        ),
        "workbook_metadata": {
            "disease_id": disease_id,
            "source_groups": source_groups,
            "generator": body.generator,
            "force": body.force,
        },
    }


def build_report_task_spec(
    body: AIStartRequest,
    *,
    country_code: str,
    report_type: str,
    report_layout: str,
    analysis_depth: str,
    reuse_strategy: str,
    reuse_report_id: Optional[int],
) -> Dict[str, Any]:
    """Validate report language and construct task metadata and input_data."""
    language = (body.language or "en").strip().lower()
    if language not in {"zh", "en"}:
        raise HTTPException(422, "Invalid language, expected 'zh' or 'en'")

    task_name = body.task_name or f"Generate {report_type.upper()} Report for {country_code}"
    description = body.description or (
        f"Report Type: {report_type}, Days: {body.days}, "
        f"Language: {body.language}, "
        f"Layout: {report_layout}, "
        f"Analysis Depth: {analysis_depth}, "
        f"Quality Threshold: {body.quality_threshold:.2f}, "
        f"Review: {'Yes' if body.enable_review else 'No'}, "
        f"Email: {'Yes' if body.send_email else 'No'}, "
        f"Reuse Failed: {'Yes' if body.reuse_from_failed else 'No'}, "
        f"Reuse Strategy: {reuse_strategy}, "
        f"Reuse Report UUID: {body.reuse_report_uuid or 'Auto'}"
    )

    return {
        "task_name": task_name,
        "description": description,
        "input_data": {
            "country": country_code,
            "country_code": country_code,
            "report_type": report_type,
            "period_start": body.period_start,
            "period_end": body.period_end,
            "language": language,
            "days": body.days,
            "enable_review": body.enable_review,
            "report_layout": report_layout,
            "analysis_depth": analysis_depth,
            "quality_threshold": body.quality_threshold,
            "send_email": body.send_email,
            "reuse_from_failed": body.reuse_from_failed,
            "reuse_strategy": reuse_strategy,
            "reuse_report_id": reuse_report_id,
            "reuse_report_uuid": (
                str(body.reuse_report_uuid) if body.reuse_report_uuid else None
            ),
        },
    }


def apply_provider_updates(
    provider: AIProviderConfig, body: ProviderUpdateRequest
) -> None:
    """Apply non-null fields from ProviderUpdateRequest onto an AIProviderConfig."""
    if body.display_name is not None:
        provider.display_name = body.display_name
    if body.api_style is not None:
        provider.api_style = body.api_style
    if body.base_url is not None:
        provider.base_url = body.base_url
    if body.api_key is not None:
        provider.api_key = body.api_key
    if body.clear_api_key:
        provider.api_key = None
    if body.organization is not None:
        provider.organization = body.organization
    if body.extra_headers is not None:
        provider.extra_headers = body.extra_headers
    if body.extra_config is not None:
        provider.extra_config = body.extra_config
    if body.is_active is not None:
        provider.is_active = body.is_active
    if body.priority is not None:
        provider.priority = body.priority


def build_new_model_extra_params(body: ModelCreateRequest) -> Dict[str, Any]:
    """Normalize extra_params, concurrency, thinking mode, and availability for a new model."""
    extra_params = _without_completion_caps(body.extra_params)
    if body.runtime_max_concurrency is not None:
        extra_params["runtime_max_concurrency"] = body.runtime_max_concurrency
    if body.thinking_mode is not None:
        extra_params = with_thinking_mode(extra_params, body.thinking_mode)
    if (
        "availability_windows" in body.model_fields_set
        or "availability_timezone" in body.model_fields_set
    ):
        extra_params = _apply_availability(
            extra_params,
            body.availability_timezone,
            [item.model_dump() for item in body.availability_windows]
            if body.availability_windows
            else [],
        )
    return extra_params


def apply_model_updates(model: AIModelConfig, body: ModelUpdateRequest) -> None:
    """Apply scalar and extra_params updates onto an AIModelConfig (excluding is_default)."""
    if body.display_name is not None:
        model.display_name = body.display_name
    if body.model_type is not None:
        model.model_type = body.model_type
    if body.api_style is not None:
        model.api_style = body.api_style
    if body.temperature is not None:
        model.temperature = body.temperature
    model.max_tokens = None
    if (
        body.extra_params is not None
        or body.thinking_mode is not None
        or "runtime_max_concurrency" in body.model_fields_set
        or "availability_windows" in body.model_fields_set
        or "availability_timezone" in body.model_fields_set
    ):
        extra_params = _without_completion_caps(
            body.extra_params
            if body.extra_params is not None
            else (model.extra_params or {})
        )
        if "runtime_max_concurrency" in body.model_fields_set:
            if body.runtime_max_concurrency is None:
                extra_params.pop("runtime_max_concurrency", None)
            else:
                extra_params["runtime_max_concurrency"] = body.runtime_max_concurrency
        if body.thinking_mode is not None:
            extra_params = with_thinking_mode(extra_params, body.thinking_mode)
        if (
            "availability_windows" in body.model_fields_set
            or "availability_timezone" in body.model_fields_set
        ):
            current = availability_view(extra_params)
            windows = (
                [item.model_dump() for item in body.availability_windows]
                if body.availability_windows is not None
                else current["availability_windows"]
            )
            timezone_name = (
                body.availability_timezone
                if "availability_timezone" in body.model_fields_set
                else current["availability_timezone"]
            )
            extra_params = _apply_availability(extra_params, timezone_name, windows)
        model.extra_params = extra_params
    elif model.extra_params:
        model.extra_params = _without_completion_caps(model.extra_params)
    if body.is_enabled is not None:
        model.is_enabled = body.is_enabled
    if body.priority is not None:
        model.priority = body.priority


async def promote_replacement_default_model(
    db: AsyncSession,
    *,
    exclude_provider_id: Optional[int] = None,
    exclude_model_id: Optional[int] = None,
) -> Optional[AIModelConfig]:
    """Select and promote the next default AIModelConfig when the current default is removed."""
    if exclude_provider_id is not None:
        replacement = (
            await db.execute(
                select(AIModelConfig)
                .where(AIModelConfig.provider_id != exclude_provider_id)
                .where(AIModelConfig.is_enabled.is_(True))
                .order_by(
                    AIModelConfig.is_enabled.desc(),
                    AIModelConfig.priority.asc(),
                    AIModelConfig.id.asc(),
                )
            )
        ).scalars().first()
        if replacement is None:
            replacement = (
                await db.execute(
                    select(AIModelConfig)
                    .where(AIModelConfig.provider_id != exclude_provider_id)
                    .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
                )
            ).scalars().first()
    else:
        query = select(AIModelConfig)
        if exclude_model_id is not None:
            query = query.where(AIModelConfig.id != exclude_model_id)
        replacement = (
            await db.execute(
                query.order_by(
                    AIModelConfig.is_enabled.desc(),
                    AIModelConfig.priority.asc(),
                    AIModelConfig.id.asc(),
                )
            )
        ).scalars().first()

    if replacement is not None:
        replacement.is_default = True
    return replacement


def build_call_logs_query(
    *,
    provider_key: Optional[str] = None,
    model_key: Optional[str] = None,
    status: Optional[str] = None,
):
    """Build filtered SQLAlchemy query for AIModelCallLog."""
    query = select(AIModelCallLog)
    if provider_key:
        query = query.where(AIModelCallLog.provider_key == provider_key)
    if model_key:
        query = query.where(AIModelCallLog.model_key == model_key)
    if status:
        query = query.where(AIModelCallLog.status == status)
    return query


def order_and_paginate_call_logs_query(query, *, page: int, page_size: int):
    """Apply canonical reverse-chronological ordering and pagination to a call-log query."""
    return (
        query.order_by(desc(AIModelCallLog.created_at), desc(AIModelCallLog.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )


def call_log_to_out(row: AIModelCallLog) -> AIModelCallLogOut:
    """Serialize one AIModelCallLog row to its API output schema."""
    return AIModelCallLogOut(
        id=row.id,
        request_id=row.request_id,
        model_key=row.model_key,
        model_name=row.model_name,
        provider_key=row.provider_key,
        agent_name=row.agent_name,
        status=row.status,
        latency_ms=row.latency_ms,
        error_type=row.error_type,
        error_message=row.error_message,
        created_at=row.created_at.isoformat(),
    )


def runtime_route_to_out(route: Dict[str, Any]) -> RuntimeRouteOut:
    """Project one enriched runtime route dict to RuntimeRouteOut."""
    return RuntimeRouteOut(
        model_id=int(route["model_id"]),
        model_key=str(route["model_key"]),
        model_name=str(route["model_name"]),
        provider_id=int(route["provider_id"]),
        provider_key=str(route["provider_key"]),
        provider_name=str(route["provider_name"]),
        api_style=str(route["api_style"]),
        base_url=route.get("base_url"),
        has_api_key=bool(route.get("api_key")),
        api_key_hint=mask_api_key(route.get("api_key")),
        priority=route.get("priority"),
        available_for_routing=bool(route.get("available_for_routing")),
        routing_status=str(route.get("routing_status") or "probe_unavailable"),
        routing_status_reason=route.get("routing_status_reason"),
        last_check_status=route.get("last_check_status"),
        last_checked_at=route.get("last_checked_at"),
        rate_limit_active=bool(route.get("rate_limit_active")),
        rate_limit_scope=route.get("rate_limit_scope"),
        rate_limit_cooldown_until=route.get("rate_limit_cooldown_until"),
        rate_limit_remaining_seconds=int(route.get("rate_limit_remaining_seconds") or 0),
        rate_limit_count=int(route.get("rate_limit_count") or 0),
        last_rate_limit_at=route.get("last_rate_limit_at"),
        runtime_failure_active=bool(route.get("runtime_failure_active")),
        runtime_failure_scope=route.get("runtime_failure_scope"),
        runtime_failure_cooldown_until=route.get("runtime_failure_cooldown_until"),
        runtime_failure_remaining_seconds=int(
            route.get("runtime_failure_remaining_seconds") or 0
        ),
        runtime_failure_kind=route.get("runtime_failure_kind"),
        runtime_failure_streak=int(route.get("runtime_failure_streak") or 0),
        runtime_failure_streak_raw=int(route.get("runtime_failure_streak_raw") or 0),
        runtime_failure_count=int(route.get("runtime_failure_count") or 0),
        runtime_timeout_count=int(route.get("runtime_timeout_count") or 0),
        runtime_success_count=int(route.get("runtime_success_count") or 0),
        runtime_degraded=bool(route.get("runtime_degraded")),
        runtime_degraded_scope=route.get("runtime_degraded_scope"),
        runtime_degraded_reason=route.get("runtime_degraded_reason"),
        runtime_latency_ewma_ms=route.get("runtime_latency_ewma_ms"),
        runtime_last_failure_at=route.get("runtime_last_failure_at"),
        runtime_last_success_at=route.get("runtime_last_success_at"),
        runtime_last_error=route.get("runtime_last_error"),
        runtime_provider_capacity=int(route.get("runtime_provider_capacity") or 0),
        runtime_provider_inflight=int(route.get("runtime_provider_inflight") or 0),
        runtime_provider_max_capacity=int(
            route.get("runtime_provider_max_capacity") or 0
        ),
        runtime_model_capacity=int(route.get("runtime_model_capacity") or 0),
        runtime_model_inflight=int(route.get("runtime_model_inflight") or 0),
    )


def _task_to_out(task: Task) -> TaskOut:
    metadata = dict(task.metadata_ or {})
    cancel_requested = bool(metadata.get("cancel_requested"))
    cancel_requested_at = (
        metadata.get("cancel_requested_at")
        if isinstance(metadata.get("cancel_requested_at"), str)
        else None
    )
    return TaskOut(
        id=task.id,
        task_uuid=task.task_uuid,
        task_name=task.task_name,
        task_type=task.task_type,
        status=task.status,
        priority=task.priority,
        progress=task.progress or 0,
        country_id=task.country_id,
        report_id=task.report_id,
        description=task.description,
        last_error=task.last_error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        actual_duration=task.actual_duration,
        workbook_count=0,
        cancel_requested=cancel_requested,
        cancel_requested_at=cancel_requested_at,
    )


def _provider_to_out(provider: AIProviderConfig) -> ProviderOut:
    rate_limit_meta = get_provider_rate_limit_state(provider)
    runtime_health = get_provider_runtime_health_state(provider)
    return ProviderOut(
        id=provider.id,
        provider_key=provider.provider_key,
        provider_name=provider.provider_name,
        display_name=provider.display_name,
        api_style=provider.api_style,
        base_url=provider.base_url,
        organization=provider.organization,
        is_active=provider.is_active,
        priority=provider.priority,
        has_api_key=bool(provider.api_key),
        api_key_hint=mask_api_key(provider.api_key),
        extra_headers=provider.extra_headers or {},
        extra_config=provider.extra_config or {},
        last_check_status=provider.last_check_status,
        last_check_message=provider.last_check_message,
        last_checked_at=(
            provider.last_checked_at.isoformat() if provider.last_checked_at else None
        ),
        rate_limit_active=rate_limit_meta["rate_limit_active"],
        rate_limit_cooldown_until=rate_limit_meta["rate_limit_cooldown_until"],
        rate_limit_remaining_seconds=rate_limit_meta["rate_limit_remaining_seconds"],
        rate_limit_count=rate_limit_meta["rate_limit_count"],
        last_rate_limit_at=rate_limit_meta["last_rate_limit_at"],
        runtime_failure_active=runtime_health["runtime_failure_active"],
        runtime_failure_cooldown_until=runtime_health["runtime_failure_cooldown_until"],
        runtime_failure_remaining_seconds=runtime_health[
            "runtime_failure_remaining_seconds"
        ],
        runtime_failure_streak=runtime_health["runtime_failure_streak"],
        runtime_timeout_count=runtime_health["runtime_timeout_count"],
        runtime_latency_ewma_ms=runtime_health["runtime_latency_ewma_ms"],
        runtime_last_error=runtime_health["runtime_last_error"],
    )


def _model_to_out(model: AIModelConfig) -> ModelOut:
    provider = model.provider
    rate_limit_meta = get_model_rate_limit_state(model, provider)
    runtime_health = get_model_runtime_health_state(model, provider)
    return ModelOut(
        id=model.id,
        provider_id=model.provider_id,
        provider_key=provider.provider_key if provider else "",
        provider_name=provider.provider_name if provider else "",
        model_key=model.model_key,
        model_name=model.model_name,
        display_name=model.display_name,
        model_type=model.model_type,
        api_style=model.api_style,
        temperature=model.temperature,
        runtime_max_concurrency=(model.extra_params or {}).get(
            "runtime_max_concurrency"
        ),
        thinking_mode=get_thinking_mode(model.extra_params),
        **availability_view(model.extra_params),
        extra_params=model.extra_params or {},
        is_enabled=model.is_enabled,
        is_default=model.is_default,
        priority=model.priority,
        last_check_status=model.last_check_status,
        last_check_message=model.last_check_message,
        last_checked_at=(
            model.last_checked_at.isoformat() if model.last_checked_at else None
        ),
        rate_limit_active=rate_limit_meta["rate_limit_active"],
        rate_limit_scope=rate_limit_meta.get("rate_limit_scope"),
        rate_limit_cooldown_until=rate_limit_meta["rate_limit_cooldown_until"],
        rate_limit_remaining_seconds=rate_limit_meta["rate_limit_remaining_seconds"],
        rate_limit_count=rate_limit_meta["rate_limit_count"],
        last_rate_limit_at=rate_limit_meta["last_rate_limit_at"],
        runtime_failure_active=runtime_health["runtime_failure_active"],
        runtime_failure_scope=runtime_health.get("runtime_failure_scope"),
        runtime_failure_cooldown_until=runtime_health["runtime_failure_cooldown_until"],
        runtime_failure_remaining_seconds=runtime_health[
            "runtime_failure_remaining_seconds"
        ],
        runtime_failure_streak=runtime_health["runtime_failure_streak"],
        runtime_timeout_count=runtime_health["runtime_timeout_count"],
        runtime_latency_ewma_ms=runtime_health["runtime_latency_ewma_ms"],
        runtime_last_error=runtime_health["runtime_last_error"],
    )


async def enqueue_disease_knowledge_runs(
    db: AsyncSession,
    body: DiseaseKnowledgeStartRequest,
    *,
    service: DiseaseKnowledgeUpdateService,
    task_manager_ref: Any,
    ensure_schema=ensure_task_type_enum_schema,
) -> DiseaseKnowledgeStartResponse:
    """Validate and enqueue per-disease knowledge update tasks."""
    await ensure_schema(db)
    requested_ids = _dedupe_disease_ids(body.disease_ids)
    if not requested_ids:
        raise HTTPException(422, "At least one disease id is required")

    priority = _normalize_priority(body.priority)
    source_groups = _normalize_source_groups(body.source)
    active_by_disease = _active_knowledge_tasks_by_disease(
        await _load_active_knowledge_tasks(db)
    )

    created_tasks: list[TaskOut] = []
    skipped: list[DiseaseKnowledgeTaskSkipped] = []

    for disease_id in requested_ids:
        disease = _find_disease_in_catalogue(service, disease_id)
        if disease is None:
            skipped.append(
                DiseaseKnowledgeTaskSkipped(
                    disease_id=disease_id,
                    reason="not_found",
                    existing_task_uuid=None,
                    existing_status=None,
                )
            )
            continue

        existing = active_by_disease.get(disease_id.upper())
        if existing is not None:
            skipped.append(
                DiseaseKnowledgeTaskSkipped(
                    disease_id=disease_id,
                    reason="already_running",
                    existing_task_uuid=existing.task_uuid,
                    existing_status=str(existing.status),
                )
            )
            continue

        spec = build_knowledge_task_spec(
            body,
            disease=disease,
            disease_id=disease_id,
            requested_count=len(requested_ids),
            source_groups=source_groups,
        )
        task = await task_manager_ref.create_task(
            task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
            task_name=spec["task_name"],
            priority=priority,
            description=spec["description"],
            input_data=spec["input_data"],
        )
        await task_manager_ref.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Task Queued",
            content=spec["workbook_content"],
            content_type="text",
            metadata=spec["workbook_metadata"],
        )
        task = (
            await task_manager_ref.update_task_status(task.task_uuid, TaskStatus.QUEUED)
            or task
        )
        created_tasks.append(_task_to_out(task))

    return DiseaseKnowledgeStartResponse(
        requested_disease_ids=requested_ids,
        created_tasks=created_tasks,
        skipped=skipped,
    )


async def enqueue_disease_knowledge_repair_runs(
    db: AsyncSession,
    body: DiseaseKnowledgeRepairRequest,
    *,
    service: DiseaseKnowledgeUpdateService,
) -> DiseaseKnowledgeRepairResponse:
    """Validate and enqueue disease-knowledge repair tasks."""
    source_groups = _normalize_source_groups(body.source)
    if body.priority is not None:
        _normalize_priority(body.priority)
    result = await service.enqueue_repair_tasks(
        db,
        source_groups=source_groups,
        force=body.force,
        generator_mode=body.generator,
        priority=body.priority,
        limit=body.limit,
        requested_by="ai-control-panel",
        initiated_via="dashboard-repair",
    )
    return DiseaseKnowledgeRepairResponse(
        created_count=result["created_count"],
        skipped_count=result["skipped_count"],
        source_groups=result["source_groups"],
        generator=result["generator"],
        force=result["force"],
        created_tasks=[_task_to_out(task) for task in result["created_tasks"]],
        skipped=[
            DiseaseKnowledgeTaskSkipped(
                disease_id=str(item.get("disease_id") or ""),
                reason=str(item.get("reason") or "skipped"),
                existing_task_uuid=item.get("existing_task_uuid"),
                existing_status=item.get("existing_status"),
            )
            for item in result["skipped"]
        ],
    )


async def enqueue_report_generation_task(
    db: AsyncSession,
    body: AIStartRequest,
    *,
    task_manager_ref: Any,
) -> TaskOut:
    """Validate report parameters, guard against overlapping runs, and enqueue GENERATE_REPORT."""
    country = (
        await db.execute(
            select(Country).where(
                func.upper(Country.code) == body.country_code.strip().upper()
            )
        )
    ).scalar_one_or_none()
    if not country:
        raise HTTPException(404, f"Country not found: {body.country_code}")

    reuse_report_id = None
    if body.reuse_report_uuid is not None:
        reuse_report_id = (
            await db.execute(
                select(Report.id).where(Report.report_uuid == body.reuse_report_uuid)
            )
        ).scalar_one_or_none()
        if reuse_report_id is None:
            raise HTTPException(404, "Reuse report not found")

    report_type = _normalize_report_type(body.report_type)
    priority = _normalize_priority(body.priority)
    reuse_strategy = _normalize_reuse_strategy(body.reuse_strategy)
    report_layout = _normalize_report_layout(body.report_layout)
    analysis_depth = _normalize_analysis_depth(body.analysis_depth)

    running = (
        await db.execute(
            select(Task).where(
                Task.task_type == TaskType.GENERATE_REPORT,
                Task.country_id == country.id,
                Task.status.in_([TaskStatus.RUNNING, TaskStatus.QUEUED]),
            )
        )
    ).scalar_one_or_none()
    if running:
        raise HTTPException(
            409,
            f"An AI task is already running for this country (task {running.task_uuid})",
        )

    spec = build_report_task_spec(
        body,
        country_code=country.code.upper(),
        report_type=report_type,
        report_layout=report_layout,
        analysis_depth=analysis_depth,
        reuse_strategy=reuse_strategy,
        reuse_report_id=reuse_report_id,
    )
    task = await task_manager_ref.create_task(
        task_type=TaskType.GENERATE_REPORT,
        task_name=spec["task_name"],
        country_id=country.id,
        priority=priority,
        description=spec["description"],
        input_data=spec["input_data"],
    )
    task = (
        await task_manager_ref.update_task_status(task.task_uuid, TaskStatus.QUEUED)
        or task
    )
    return _task_to_out(task)


async def list_provider_outputs(db: AsyncSession) -> List[ProviderOut]:
    rows = (
        await db.execute(
            select(AIProviderConfig).order_by(
                AIProviderConfig.priority.asc(), AIProviderConfig.id.asc()
            )
        )
    ).scalars().all()
    return [_provider_to_out(item) for item in rows]


async def create_provider_record(
    db: AsyncSession, body: ProviderCreateRequest
) -> ProviderOut:
    exists = (
        await db.execute(
            select(AIProviderConfig).where(
                AIProviderConfig.provider_key == body.provider_key
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Provider key already exists: {body.provider_key}")

    provider = AIProviderConfig(
        provider_key=body.provider_key,
        provider_name=body.provider_name,
        display_name=body.display_name,
        api_style=body.api_style,
        base_url=body.base_url,
        api_key=body.api_key,
        organization=body.organization,
        extra_headers=body.extra_headers,
        extra_config=body.extra_config,
        is_active=body.is_active,
        priority=body.priority,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return _provider_to_out(provider)


async def update_provider_record(
    db: AsyncSession, provider_key: str, body: ProviderUpdateRequest
) -> ProviderOut:
    provider = (
        await db.execute(
            select(AIProviderConfig).where(
                AIProviderConfig.provider_key == provider_key
            )
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(404, "Provider not found")

    apply_provider_updates(provider, body)
    await db.commit()
    await db.refresh(provider)
    return _provider_to_out(provider)


async def delete_provider_record(db: AsyncSession, provider_key: str) -> Dict[str, Any]:
    provider = (
        await db.execute(
            select(AIProviderConfig).where(
                AIProviderConfig.provider_key == provider_key
            )
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(404, "Provider not found")

    models_to_delete = (
        await db.execute(
            select(AIModelConfig)
            .where(AIModelConfig.provider_id == provider.id)
            .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
        )
    ).scalars().all()
    had_default_model = any(model.is_default for model in models_to_delete)

    await db.delete(provider)
    await db.flush()

    replacement = None
    if had_default_model:
        replacement = await promote_replacement_default_model(
            db, exclude_provider_id=provider.id
        )

    await db.commit()
    return {
        "ok": True,
        "provider_key": provider.provider_key,
        "removed_model_count": len(models_to_delete),
        "default_promoted_model_key": (
            replacement.model_key if replacement is not None else None
        ),
    }


async def list_model_outputs(db: AsyncSession) -> List[ModelOut]:
    rows = (
        await db.execute(
            select(AIModelConfig)
            .options(selectinload(AIModelConfig.provider))
            .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
        )
    ).scalars().all()
    return [_model_to_out(item) for item in rows]


async def create_model_record(db: AsyncSession, body: ModelCreateRequest) -> ModelOut:
    provider = (
        await db.execute(
            select(AIProviderConfig).where(
                AIProviderConfig.provider_key == body.provider_key
            )
        )
    ).scalar_one_or_none()
    if provider is None:
        raise HTTPException(404, "Provider not found")

    model_key = body.model_key or f"{provider.provider_key}:{body.model_name}"
    exists = (
        await db.execute(
            select(AIModelConfig).where(AIModelConfig.model_key == model_key)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Model key already exists: {model_key}")

    if body.is_default:
        for old in (
            await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_default.is_(True))
            )
        ).scalars().all():
            old.is_default = False

    model = AIModelConfig(
        provider_id=provider.id,
        model_key=model_key,
        model_name=body.model_name,
        display_name=body.display_name or body.model_name,
        model_type=body.model_type,
        api_style=body.api_style,
        temperature=body.temperature,
        max_tokens=None,
        extra_params=build_new_model_extra_params(body),
        is_enabled=body.is_enabled,
        is_default=body.is_default,
        priority=body.priority,
    )
    db.add(model)
    await db.commit()

    model = (
        await db.execute(
            select(AIModelConfig)
            .options(selectinload(AIModelConfig.provider))
            .where(AIModelConfig.id == model.id)
        )
    ).scalar_one()
    return _model_to_out(model)


async def update_model_record(
    db: AsyncSession, model_key: str, body: ModelUpdateRequest
) -> ModelOut:
    model = (
        await db.execute(
            select(AIModelConfig)
            .options(selectinload(AIModelConfig.provider))
            .where(AIModelConfig.model_key == model_key)
            .with_for_update(of=AIModelConfig)
        )
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Model not found")

    apply_model_updates(model, body)

    if body.is_default is True:
        for old in (
            await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_default.is_(True))
            )
        ).scalars().all():
            old.is_default = False
        model.is_default = True
    elif body.is_default is False:
        model.is_default = False

    await db.commit()
    await db.refresh(model)
    return _model_to_out(model)


async def delete_model_record(db: AsyncSession, model_key: str) -> Dict[str, Any]:
    model = (
        await db.execute(
            select(AIModelConfig).where(AIModelConfig.model_key == model_key)
        )
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Model not found")

    deleted_model_key = model.model_key
    was_default = bool(model.is_default)

    await db.delete(model)
    await db.flush()

    replacement = None
    if was_default:
        replacement = await promote_replacement_default_model(
            db, exclude_model_id=model.id
        )

    await db.commit()
    return {
        "ok": True,
        "deleted_model_key": deleted_model_key,
        "new_default_model_key": (
            replacement.model_key if replacement is not None else None
        ),
    }


async def fetch_call_logs_page(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    provider_key: Optional[str] = None,
    model_key: Optional[str] = None,
    status: Optional[str] = None,
) -> tuple[List[AIModelCallLogOut], int, int]:
    query = build_call_logs_query(
        provider_key=provider_key,
        model_key=model_key,
        status=status,
    )
    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            order_and_paginate_call_logs_query(query, page=page, page_size=page_size)
        )
    ).scalars().all()
    return [call_log_to_out(row) for row in rows], int(total), (page - 1) * page_size
