"""AI router: task launch and model-center APIs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..deps import get_db
from ..schemas.task import TaskOut
from src.ai.model_center import (
    bootstrap_model_center_from_env,
    check_all_models,
    check_model_by_id,
    check_provider_by_id,
    get_model_rate_limit_state,
    get_provider_rate_limit_state,
    get_runtime_routes,
    mask_api_key,
)
from src.core.db_schema import ensure_task_type_enum_schema
from src.core.task_manager import task_manager
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.domain.country import Country
from src.domain.report import ReportType
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType
from src.services.disease_duplicate_audit_service import DiseaseDuplicateAuditService
from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService, SOURCE_GROUPS, expand_sources

router = APIRouter()


class AIStartRequest(BaseModel):
    """Body for POST /ai/start."""

    country_id: int = Field(..., ge=1, description="Country DB id")
    report_type: str = Field("monthly", description="Report type: daily / weekly / monthly / special")
    period_start: Optional[str] = Field(None, description="ISO datetime, optional")
    period_end: Optional[str] = Field(None, description="ISO datetime, optional")
    language: str = Field("en", description="Report language: zh or en")
    days: int = Field(365, ge=1, le=3650, description="Fallback period in days")
    enable_review: bool = Field(True, description="Enable reviewer agent")
    report_layout: str = Field(
        "analytical_v3",
        description="Report layout: analytical_v3 | structured | legacy",
    )
    analysis_depth: str = Field(
        "deep",
        description="Analytical v3 depth: deep | deterministic",
    )
    quality_threshold: float = Field(
        0.85,
        ge=0.0,
        le=1.0,
        description="Minimum quality gate score for automatic approval",
    )
    send_email: bool = Field(
        False,
        description="Send the completed report email to the centralized Settings recipients after generation",
    )
    reuse_from_failed: bool = Field(True, description="Reuse partial output from failed/generating tasks in same scope")
    reuse_strategy: str = Field(
        "auto",
        description="Reuse strategy: auto | safe | resume | manual",
    )
    reuse_report_id: Optional[int] = Field(
        None,
        ge=1,
        description="Optional explicit report ID when reuse_strategy=manual",
    )
    priority: str = Field("normal", description="Task priority")
    task_name: Optional[str] = Field(None, description="Optional custom task name")
    description: Optional[str] = Field(None, description="Optional task description")


class DiseaseKnowledgeCatalogueItem(BaseModel):
    disease_id: str
    name_en: Optional[str] = None
    name_zh: Optional[str] = None
    category: Optional[str] = None
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    knowledge_status: str = "fallback"
    knowledge_updated_at: Optional[str] = None
    published_languages: List[str] = Field(default_factory=list)
    source_count: int = 0
    brief_statuses: Dict[str, str] = Field(default_factory=dict)


class DiseaseKnowledgeTaskSkipped(BaseModel):
    disease_id: str
    reason: str
    existing_task_uuid: Optional[str] = None
    existing_status: Optional[str] = None


class DiseaseKnowledgeStartRequest(BaseModel):
    disease_ids: List[str] = Field(..., min_length=1)
    source: List[str] = Field(default_factory=list, description="Source groups: who / search / wikidata / wikipedia / pubmed / msd")
    force: bool = False
    generator: str = Field("ai", description="ai / auto / template")
    priority: str = Field("normal")
    task_name: Optional[str] = Field(None, description="Optional batch task name prefix")
    description: Optional[str] = Field(None, description="Optional task description")


class DiseaseKnowledgeStartResponse(BaseModel):
    requested_disease_ids: List[str]
    created_tasks: List[TaskOut]
    skipped: List[DiseaseKnowledgeTaskSkipped] = Field(default_factory=list)


class DiseaseDuplicateAuditRequest(BaseModel):
    include_ai: bool = Field(True, description="Ask the model center to classify findings")
    include_new_disease_candidates: bool = Field(
        True,
        description="Scan current source data for unmapped disease terms that may need new standard diseases",
    )
    max_ai_candidates: int = Field(40, ge=1, le=100)


class DiseaseDuplicateAuditStatusRequest(BaseModel):
    include_new_disease_candidates: bool = True


class DiseaseKnowledgeSourceDetail(BaseModel):
    id: int
    disease_id: str
    source_type: str
    source_name: str
    url: str
    resolved_url: Optional[str] = None
    title: Optional[str] = None
    license: Optional[str] = None
    status: str
    language: str
    raw_excerpt: Optional[str] = None
    content_text: Optional[str] = None
    content_sections: List[Dict[str, Any]] = Field(default_factory=list)
    raw_excerpt_hash: Optional[str] = None
    fetched_at: Optional[str] = None
    review_status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DiseaseKnowledgeBriefDetail(BaseModel):
    language: str
    status: str
    source_confidence: str
    updated_at: Optional[str] = None
    brief: str
    definition: Optional[str] = None
    clinical_features: Optional[str] = None
    clinical_summary: Optional[str] = None
    epidemiology: Optional[str] = None
    transmission: Optional[str] = None
    prevention: Optional[str] = None
    surveillance_note: Optional[str] = None
    risk_groups: Optional[str] = None
    disclaimer: Optional[str] = None
    model: Optional[str] = None
    quality_score: Optional[float] = None
    review_notes: Optional[str] = None
    source_ids: List[int] = Field(default_factory=list)
    source_attribution: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DiseaseKnowledgeDetail(BaseModel):
    disease_id: str
    name_en: Optional[str] = None
    name_zh: Optional[str] = None
    category: Optional[str] = None
    icd_10: Optional[str] = None
    icd_11: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    knowledge_status: str = "fallback"
    knowledge_updated_at: Optional[str] = None
    published_languages: List[str] = Field(default_factory=list)
    source_count: int = 0
    brief_statuses: Dict[str, str] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    briefs: List[DiseaseKnowledgeBriefDetail] = Field(default_factory=list)
    sources: List[DiseaseKnowledgeSourceDetail] = Field(default_factory=list)


@router.post("/ai/disease-duplicate-audit/run", response_model=Dict[str, Any])
@router.post("/ai/disease-audit/run", response_model=Dict[str, Any])
async def run_disease_duplicate_audit(body: DiseaseDuplicateAuditRequest):
    service = DiseaseDuplicateAuditService()
    run_id = str(uuid4())
    logs: list[dict[str, Any]] = []
    service.record_event(
        logs,
        run_id,
        "run_started",
        "Disease duplicate audit run started.",
        include_ai=body.include_ai,
        include_new_disease_candidates=body.include_new_disease_candidates,
        max_ai_candidates=body.max_ai_candidates,
    )
    audit = service.run_local_audit(
        include_new_disease_candidates=body.include_new_disease_candidates,
    )
    audit["run_id"] = run_id
    audit["logs"] = logs
    service.record_event(
        logs,
        run_id,
        "local_audit_completed",
        "Local disease duplicate audit completed.",
        summary=audit.get("summary"),
    )
    if body.include_ai:
        try:
            audit["ai_review"] = await service.run_ai_review(
                audit,
                max_candidates=body.max_ai_candidates,
                run_id=run_id,
                logs=logs,
            )
        except Exception as exc:
            service.record_event(
                logs,
                run_id,
                "ai_review_exception",
                "Disease audit AI review failed and local audit result will be returned.",
                level="error",
                error=str(exc),
            )
            audit["ai_review"] = {
                "status": "failed",
                "summary": {
                    "merge": 0,
                    "keep_separate": 0,
                    "add_standard_disease": 0,
                    "needs_human_review": 0,
                },
                "recommendations": [],
                "warnings": [
                    str(exc),
                    "Local audit completed, but AI review failed because no model-center route returned a usable chat completion.",
                    "Open the AI Models page and test/fix provider base URLs, keys, quota, and enabled model routes.",
                ],
            }
    else:
        service.record_event(
            logs,
            run_id,
            "ai_review_skipped",
            "Disease audit AI review was skipped by request.",
        )
    service.record_event(
        logs,
        run_id,
        "run_completed",
        "Disease duplicate audit run completed.",
        ai_status=(audit.get("ai_review") or {}).get("status", "completed") if audit.get("ai_review") else "skipped",
    )
    audit["logs"] = logs
    return audit


@router.get("/ai/disease-duplicate-audit/status", response_model=Dict[str, Any])
@router.get("/ai/disease-audit/status", response_model=Dict[str, Any])
async def get_disease_duplicate_audit_status(include_new_disease_candidates: bool = True):
    service = DiseaseDuplicateAuditService()
    return await service.status(
        include_new_disease_candidates=include_new_disease_candidates,
    )


@router.get("/ai/disease-duplicate-audit/logs", response_model=List[Dict[str, Any]])
@router.get("/ai/disease-audit/logs", response_model=List[Dict[str, Any]])
async def list_disease_duplicate_audit_logs(limit: int = Query(100, ge=1, le=500)):
    return DiseaseDuplicateAuditService.read_audit_logs(limit=limit)


@router.get("/ai/disease-knowledge/catalogue", response_model=List[DiseaseKnowledgeCatalogueItem])
async def list_disease_knowledge_catalogue(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    service = DiseaseKnowledgeUpdateService()
    return await service.list_catalogue(db, search=search)


@router.post("/ai/disease-knowledge/start", response_model=DiseaseKnowledgeStartResponse, status_code=201)
async def start_disease_knowledge_tasks(
    body: DiseaseKnowledgeStartRequest,
    db: AsyncSession = Depends(get_db),
):
    service = DiseaseKnowledgeUpdateService()
    await ensure_task_type_enum_schema(db)
    requested_ids = _dedupe_disease_ids(body.disease_ids)
    if not requested_ids:
        raise HTTPException(422, "At least one disease id is required")

    priority = _normalize_priority(body.priority)
    source_groups = _normalize_source_groups(body.source)
    active_tasks = await _load_active_knowledge_tasks(db)
    active_by_disease = _active_knowledge_tasks_by_disease(active_tasks)

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

        task_name = body.task_name or f"Update {disease['name_en']} knowledge"
        if body.task_name and len(requested_ids) > 1:
            task_name = f"{body.task_name} · {disease['name_en']}"

        description = body.description or (
            f"Disease: {disease_id}, "
            f"Sources: {', '.join(source_groups) or 'default'}, "
            f"Force: {'Yes' if body.force else 'No'}, "
            f"Generator: {body.generator}"
        )

        task = await task_manager.create_task(
            task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
            task_name=task_name,
            priority=priority,
            description=description,
            input_data={
                "disease_id": disease_id,
                "disease_ids": [disease_id],
                "source_groups": source_groups,
                "source": source_groups,
                "force": body.force,
                "generator": body.generator,
                "initiated_via": "dashboard",
                "requested_by": "ai-control-panel",
            },
        )
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Task Queued",
            content=(
                f"Queued from AI control panel for disease {disease_id} "
                f"with sources {', '.join(source_groups) or 'default'}."
            ),
            content_type="text",
            metadata={
                "disease_id": disease_id,
                "source_groups": source_groups,
                "generator": body.generator,
                "force": body.force,
            },
        )
        task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
        created_tasks.append(_task_to_out(task))

    if not created_tasks and skipped:
        return DiseaseKnowledgeStartResponse(
            requested_disease_ids=requested_ids,
            created_tasks=[],
            skipped=skipped,
        )

    return DiseaseKnowledgeStartResponse(
        requested_disease_ids=requested_ids,
        created_tasks=created_tasks,
        skipped=skipped,
    )


@router.get("/ai/disease-knowledge/diseases/{disease_id}", response_model=DiseaseKnowledgeDetail)
async def get_disease_knowledge_detail(
    disease_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DiseaseKnowledgeUpdateService()
    try:
        payload = await service.get_detail(db, disease_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DiseaseKnowledgeDetail(**payload)


class ProviderCreateRequest(BaseModel):
    provider_key: str = Field(..., min_length=2, max_length=120)
    provider_name: str = Field(..., min_length=2, max_length=80)
    display_name: str = Field(..., min_length=2, max_length=200)
    api_style: str = Field("openai_compatible")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    organization: Optional[str] = None
    extra_headers: Dict[str, Any] = Field(default_factory=dict)
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    priority: int = 100


class ProviderBootstrapRequest(BaseModel):
    force: bool = False


class ProviderUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    api_style: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    organization: Optional[str] = None
    extra_headers: Optional[Dict[str, Any]] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


class ProviderOut(BaseModel):
    id: int
    provider_key: str
    provider_name: str
    display_name: str
    api_style: str
    base_url: Optional[str] = None
    organization: Optional[str] = None
    is_active: bool
    priority: int
    has_api_key: bool
    api_key_hint: Optional[str] = None
    extra_headers: Dict[str, Any] = {}
    extra_config: Dict[str, Any] = {}
    last_check_status: str
    last_check_message: Optional[str] = None
    last_checked_at: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None


class ModelCreateRequest(BaseModel):
    provider_id: int
    model_name: str = Field(..., min_length=1, max_length=120)
    display_name: Optional[str] = None
    model_key: Optional[str] = None
    model_type: str = "chat"
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    is_default: bool = False
    priority: int = 100


class ModelUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    model_type: Optional[str] = None
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_params: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    priority: Optional[int] = None


class ModelOut(BaseModel):
    id: int
    provider_id: int
    provider_key: str
    provider_name: str
    model_key: str
    model_name: str
    display_name: str
    model_type: str
    api_style: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra_params: Dict[str, Any] = {}
    is_enabled: bool
    is_default: bool
    priority: int
    last_check_status: str
    last_check_message: Optional[str] = None
    last_checked_at: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_scope: Optional[str] = None
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None


class RuntimeRouteOut(BaseModel):
    model_id: int
    model_key: str
    model_name: str
    provider_id: int
    provider_key: str
    provider_name: str
    api_style: str
    base_url: Optional[str] = None
    has_api_key: bool
    api_key_hint: Optional[str] = None
    priority: Optional[int] = None
    available_for_routing: bool = False
    last_check_status: Optional[str] = None
    rate_limit_active: bool = False
    rate_limit_scope: Optional[str] = None
    rate_limit_cooldown_until: Optional[str] = None
    rate_limit_remaining_seconds: int = 0
    rate_limit_count: int = 0
    last_rate_limit_at: Optional[str] = None


@router.post("/ai/start", response_model=TaskOut, status_code=201)
async def start_ai_task(
    body: AIStartRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a GENERATE_REPORT task and enqueue it for external worker execution."""
    country = (
        await db.execute(select(Country).where(Country.id == body.country_id))
    ).scalar_one_or_none()
    if not country:
        raise HTTPException(404, f"Country not found: {body.country_id}")

    report_type = _normalize_report_type(body.report_type)
    priority = _normalize_priority(body.priority)
    reuse_strategy = _normalize_reuse_strategy(body.reuse_strategy)
    report_layout = _normalize_report_layout(body.report_layout)
    analysis_depth = _normalize_analysis_depth(body.analysis_depth)

    running_q = select(Task).where(
        Task.task_type == TaskType.GENERATE_REPORT,
        Task.country_id == body.country_id,
        Task.status.in_([TaskStatus.RUNNING, TaskStatus.QUEUED]),
    )
    running = (await db.execute(running_q)).scalar_one_or_none()
    if running:
        raise HTTPException(
            409,
            f"An AI task is already running for this country (task {running.task_uuid})",
        )

    country_code = country.code.upper()
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
        f"Reuse Report ID: {body.reuse_report_id or 'Auto'}"
    )

    language = (body.language or "en").strip().lower()
    if language not in {"zh", "en"}:
        raise HTTPException(422, "Invalid language, expected 'zh' or 'en'")

    task = await task_manager.create_task(
        task_type=TaskType.GENERATE_REPORT,
        task_name=task_name,
        country_id=body.country_id,
        priority=priority,
        description=description,
        input_data={
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
            "reuse_report_id": body.reuse_report_id,
        },
    )

    task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task

    return _task_to_out(task)


@router.get("/ai/models/providers", response_model=List[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    rows = (
        await db.execute(
            select(AIProviderConfig).order_by(AIProviderConfig.priority.asc(), AIProviderConfig.id.asc())
        )
    ).scalars().all()
    return [_provider_to_out(item) for item in rows]


@router.post("/ai/models/providers", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderCreateRequest, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    exists = (
        await db.execute(select(AIProviderConfig).where(AIProviderConfig.provider_key == body.provider_key))
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


@router.put("/ai/models/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: int, body: ProviderUpdateRequest, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    provider = await db.get(AIProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found")

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

    await db.commit()
    await db.refresh(provider)
    return _provider_to_out(provider)


@router.post("/ai/models/providers/bootstrap")
async def bootstrap_providers(body: ProviderBootstrapRequest):
    await bootstrap_model_center_from_env(force=body.force)
    return {"ok": True, "force": body.force}


@router.delete("/ai/models/providers/{provider_id}")
async def delete_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    provider = await db.get(AIProviderConfig, provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found")

    models_to_delete = (
        await db.execute(
            select(AIModelConfig)
            .where(AIModelConfig.provider_id == provider_id)
            .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
        )
    ).scalars().all()

    had_default_model = any(model.is_default for model in models_to_delete)

    await db.delete(provider)
    await db.flush()

    replacement = None
    if had_default_model:
        replacement = (
            await db.execute(
                select(AIModelConfig)
                .where(AIModelConfig.provider_id != provider_id)
                .where(AIModelConfig.is_enabled.is_(True))
                .order_by(AIModelConfig.is_enabled.desc(), AIModelConfig.priority.asc(), AIModelConfig.id.asc())
            )
        ).scalars().first()

        if replacement is None:
            replacement = (
                await db.execute(
                    select(AIModelConfig)
                    .where(AIModelConfig.provider_id != provider_id)
                    .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
                )
            ).scalars().first()

        if replacement is not None:
            replacement.is_default = True

    await db.commit()
    return {
        "ok": True,
        "provider_key": provider.provider_key,
        "removed_model_count": len(models_to_delete),
        "default_promoted_model_id": replacement.id if replacement is not None else None,
        "default_promoted_model_key": replacement.model_key if replacement is not None else None,
    }


@router.post("/ai/models/providers/{provider_id}/test")
async def test_provider(provider_id: int):
    await bootstrap_model_center_from_env(force=False)
    return await check_provider_by_id(provider_id)


@router.get("/ai/models", response_model=List[ModelOut])
async def list_models(db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    rows = (
        await db.execute(
            select(AIModelConfig)
            .options(selectinload(AIModelConfig.provider))
            .order_by(AIModelConfig.priority.asc(), AIModelConfig.id.asc())
        )
    ).scalars().all()
    return [_model_to_out(item) for item in rows]


@router.post("/ai/models", response_model=ModelOut, status_code=201)
async def create_model(body: ModelCreateRequest, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    provider = await db.get(AIProviderConfig, body.provider_id)
    if provider is None:
        raise HTTPException(404, "Provider not found")

    model_key = body.model_key or f"{provider.provider_key}:{body.model_name}"
    exists = (
        await db.execute(select(AIModelConfig).where(AIModelConfig.model_key == model_key))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Model key already exists: {model_key}")

    if body.is_default:
        for old in (await db.execute(select(AIModelConfig).where(AIModelConfig.is_default.is_(True)))).scalars().all():
            old.is_default = False

    model = AIModelConfig(
        provider_id=provider.id,
        model_key=model_key,
        model_name=body.model_name,
        display_name=body.display_name or body.model_name,
        model_type=body.model_type,
        api_style=body.api_style,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        extra_params=body.extra_params,
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


@router.put("/ai/models/{model_id}", response_model=ModelOut)
async def update_model(model_id: int, body: ModelUpdateRequest, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    model = (
        await db.execute(
            select(AIModelConfig)
            .options(selectinload(AIModelConfig.provider))
            .where(AIModelConfig.id == model_id)
        )
    ).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Model not found")

    if body.display_name is not None:
        model.display_name = body.display_name
    if body.model_type is not None:
        model.model_type = body.model_type
    if body.api_style is not None:
        model.api_style = body.api_style
    if body.temperature is not None:
        model.temperature = body.temperature
    if body.max_tokens is not None:
        model.max_tokens = body.max_tokens
    if body.extra_params is not None:
        model.extra_params = body.extra_params
    if body.is_enabled is not None:
        model.is_enabled = body.is_enabled
    if body.priority is not None:
        model.priority = body.priority

    if body.is_default is True:
        for old in (await db.execute(select(AIModelConfig).where(AIModelConfig.is_default.is_(True)))).scalars().all():
            old.is_default = False
        model.is_default = True
    elif body.is_default is False:
        model.is_default = False

    await db.commit()
    await db.refresh(model)
    return _model_to_out(model)


@router.delete("/ai/models/{model_id}")
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)

    model = await db.get(AIModelConfig, model_id)
    if model is None:
        raise HTTPException(404, "Model not found")

    deleted_model_key = model.model_key
    was_default = bool(model.is_default)

    await db.delete(model)
    await db.flush()

    replacement = None
    if was_default:
        replacement = (
            await db.execute(
                select(AIModelConfig)
                .where(AIModelConfig.id != model_id)
                .order_by(AIModelConfig.is_enabled.desc(), AIModelConfig.priority.asc(), AIModelConfig.id.asc())
            )
        ).scalars().first()
        if replacement is not None:
            replacement.is_default = True

    await db.commit()
    return {
        "ok": True,
        "deleted_model_key": deleted_model_key,
        "new_default_model_key": replacement.model_key if replacement is not None else None,
    }


@router.post("/ai/models/{model_id}/test")
async def test_model(model_id: int):
    await bootstrap_model_center_from_env(force=False)
    return await check_model_by_id(model_id)


@router.post("/ai/models/check-all")
async def test_all_models():
    await bootstrap_model_center_from_env(force=False)
    return await check_all_models()


@router.get("/ai/models/runtime", response_model=List[RuntimeRouteOut])
async def list_runtime_routes():
    routes = await get_runtime_routes()
    return [
        RuntimeRouteOut(
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
            last_check_status=route.get("last_check_status"),
            rate_limit_active=bool(route.get("rate_limit_active")),
            rate_limit_scope=route.get("rate_limit_scope"),
            rate_limit_cooldown_until=route.get("rate_limit_cooldown_until"),
            rate_limit_remaining_seconds=int(route.get("rate_limit_remaining_seconds") or 0),
            rate_limit_count=int(route.get("rate_limit_count") or 0),
            last_rate_limit_at=route.get("last_rate_limit_at"),
        )
        for route in routes
    ]


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
        raise HTTPException(422, f"Invalid reuse_strategy '{value}'. Allowed values: {joined}")
    return normalized


def _normalize_report_layout(value: str) -> str:
    normalized = (value or "analytical_v3").strip().lower()
    allowed = {"analytical_v3", "structured", "legacy"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(422, f"Invalid report_layout '{value}'. Allowed values: {joined}")
    return normalized


def _normalize_analysis_depth(value: str) -> str:
    normalized = (value or "deep").strip().lower()
    allowed = {"deep", "deterministic"}
    if normalized not in allowed:
        joined = ", ".join(sorted(allowed))
        raise HTTPException(422, f"Invalid analysis_depth '{value}'. Allowed values: {joined}")
    return normalized


def _normalize_source_groups(values: list[str] | None) -> list[str]:
    cleaned = [str(value).strip().lower() for value in (values or []) if str(value).strip()]
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
            candidates.extend(str(value).strip().upper() for value in disease_ids if str(value).strip())
        if disease_id:
            candidates.append(str(disease_id).strip().upper())
        for candidate in candidates:
            if candidate and candidate not in result:
                result[candidate] = task
    return result


def _find_disease_in_catalogue(service: DiseaseKnowledgeUpdateService, disease_id: str) -> dict[str, Any] | None:
    wanted = str(disease_id or "").strip().upper()
    for disease in service.load_standard_diseases():
        if str(disease.get("disease_id") or "").upper() == wanted:
            return disease
    return None


def _task_to_out(task: Task) -> TaskOut:
    metadata = dict(task.metadata_ or {})
    cancel_requested = bool(metadata.get("cancel_requested"))
    cancel_requested_at = metadata.get("cancel_requested_at") if isinstance(metadata.get("cancel_requested_at"), str) else None
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
        last_checked_at=provider.last_checked_at.isoformat() if provider.last_checked_at else None,
        rate_limit_active=rate_limit_meta["rate_limit_active"],
        rate_limit_cooldown_until=rate_limit_meta["rate_limit_cooldown_until"],
        rate_limit_remaining_seconds=rate_limit_meta["rate_limit_remaining_seconds"],
        rate_limit_count=rate_limit_meta["rate_limit_count"],
        last_rate_limit_at=rate_limit_meta["last_rate_limit_at"],
    )


def _model_to_out(model: AIModelConfig) -> ModelOut:
    provider = model.provider
    rate_limit_meta = get_model_rate_limit_state(model, provider)
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
        max_tokens=model.max_tokens,
        extra_params=model.extra_params or {},
        is_enabled=model.is_enabled,
        is_default=model.is_default,
        priority=model.priority,
        last_check_status=model.last_check_status,
        last_check_message=model.last_check_message,
        last_checked_at=model.last_checked_at.isoformat() if model.last_checked_at else None,
        rate_limit_active=rate_limit_meta["rate_limit_active"],
        rate_limit_scope=rate_limit_meta.get("rate_limit_scope"),
        rate_limit_cooldown_until=rate_limit_meta["rate_limit_cooldown_until"],
        rate_limit_remaining_seconds=rate_limit_meta["rate_limit_remaining_seconds"],
        rate_limit_count=rate_limit_meta["rate_limit_count"],
        last_rate_limit_at=rate_limit_meta["last_rate_limit_at"],
    )
