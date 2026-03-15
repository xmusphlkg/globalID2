"""AI router: task launch and model-center APIs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
    get_active_model_routes,
    mask_api_key,
)
from src.core.task_manager import task_manager
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.domain.country import Country
from src.domain.report import ReportType
from src.domain.task import Task, TaskPriority, TaskStatus, TaskType

router = APIRouter()


class AIStartRequest(BaseModel):
    """Body for POST /ai/start."""

    country_id: int = Field(..., ge=1, description="Country DB id")
    report_type: str = Field("monthly", description="Report type: daily / weekly / monthly / special")
    period_start: Optional[str] = Field(None, description="ISO datetime, optional")
    period_end: Optional[str] = Field(None, description="ISO datetime, optional")
    days: int = Field(365, ge=1, le=3650, description="Fallback period in days")
    enable_review: bool = Field(True, description="Enable reviewer agent")
    send_email: bool = Field(False, description="Send email after generation")
    priority: str = Field("normal", description="Task priority")
    task_name: Optional[str] = Field(None, description="Optional custom task name")
    description: Optional[str] = Field(None, description="Optional task description")


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


@router.post("/ai/start", response_model=TaskOut, status_code=201)
async def start_ai_task(
    body: AIStartRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a GENERATE_REPORT task and execute it in the background."""
    country = (
        await db.execute(select(Country).where(Country.id == body.country_id))
    ).scalar_one_or_none()
    if not country:
        raise HTTPException(404, f"Country not found: {body.country_id}")

    report_type = _normalize_report_type(body.report_type)
    priority = _normalize_priority(body.priority)

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
        f"Review: {'Yes' if body.enable_review else 'No'}, "
        f"Email: {'Yes' if body.send_email else 'No'}"
    )

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
            "days": body.days,
            "enable_review": body.enable_review,
            "send_email": body.send_email,
        },
    )

    background_tasks.add_task(_execute_in_background, task.task_uuid)

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
    routes = await get_active_model_routes()
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
        )
        for route in routes
    ]


async def _execute_in_background(task_uuid: str) -> None:
    from src.services.task_executor import execute_task_background

    await execute_task_background(task_uuid)


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


def _task_to_out(task: Task) -> TaskOut:
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
    )


def _provider_to_out(provider: AIProviderConfig) -> ProviderOut:
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
    )


def _model_to_out(model: AIModelConfig) -> ModelOut:
    provider = model.provider
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
    )
