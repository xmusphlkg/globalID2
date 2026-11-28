"""AI router: task launch and model-center APIs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.model_center import (
    bootstrap_model_center_from_env,
    discover_provider_models_by_id,
    get_runtime_routes,
    mask_api_key as mask_api_key,
)
from src.ai.runtime_metrics import read_runtime_metrics
from src.core.db_schema import ensure_task_type_enum_schema
from src.core.task_manager import task_manager
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.services.ai_content_governance_service import ai_content_governance_service
from src.services.disease_duplicate_audit_service import DiseaseDuplicateAuditService
from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService
from src.services.model_operations import run_worker_operation

from ..deps import get_db
from ..schemas.ai_tasks import (
    AIContentGovernanceRequest as AIContentGovernanceRequest,
    AIContentGovernanceResponse as AIContentGovernanceResponse,
    AIStartRequest as AIStartRequest,
    DiseaseDuplicateAuditRequest as DiseaseDuplicateAuditRequest,
    DiseaseDuplicateAuditStatusRequest as DiseaseDuplicateAuditStatusRequest,
    KnowledgeRepairRetryRequest as KnowledgeRepairRetryRequest,
)
from ..schemas.disease_knowledge import (
    DiseaseKnowledgeBriefDetail as DiseaseKnowledgeBriefDetail,
    DiseaseKnowledgeCatalogueItem as DiseaseKnowledgeCatalogueItem,
    DiseaseKnowledgeDetail as DiseaseKnowledgeDetail,
    DiseaseKnowledgeRepairRequest as DiseaseKnowledgeRepairRequest,
    DiseaseKnowledgeRepairResponse as DiseaseKnowledgeRepairResponse,
    DiseaseKnowledgeSourceDetail as DiseaseKnowledgeSourceDetail,
    DiseaseKnowledgeStartRequest as DiseaseKnowledgeStartRequest,
    DiseaseKnowledgeStartResponse as DiseaseKnowledgeStartResponse,
    DiseaseKnowledgeTaskSkipped as DiseaseKnowledgeTaskSkipped,
)
from ..schemas.model_center import (
    AIModelCallLogOut as AIModelCallLogOut,
    AvailabilityWindowIn as AvailabilityWindowIn,
    ModelCreateRequest as ModelCreateRequest,
    ModelOut as ModelOut,
    ModelUpdateRequest as ModelUpdateRequest,
    ProviderBootstrapRequest as ProviderBootstrapRequest,
    ProviderCreateRequest as ProviderCreateRequest,
    ProviderOut as ProviderOut,
    ProviderUpdateRequest as ProviderUpdateRequest,
    RuntimeRouteOut as RuntimeRouteOut,
    ThinkingMode as ThinkingMode,
)
from ..schemas.task import TaskOut
from ..services.ai_model_center_service import (
    _active_knowledge_tasks_by_disease as _active_knowledge_tasks_by_disease,
    _apply_availability as _apply_availability,
    _dedupe_disease_ids as _dedupe_disease_ids,
    _find_disease_in_catalogue as _find_disease_in_catalogue,
    _load_active_knowledge_tasks as _load_active_knowledge_tasks,
    _model_to_out as _model_to_out,
    _normalize_analysis_depth as _normalize_analysis_depth,
    _normalize_priority as _normalize_priority,
    _normalize_report_layout as _normalize_report_layout,
    _normalize_report_type as _normalize_report_type,
    _normalize_reuse_strategy as _normalize_reuse_strategy,
    _normalize_source_groups as _normalize_source_groups,
    _provider_to_out as _provider_to_out,
    _task_to_out as _task_to_out,
    _without_completion_caps as _without_completion_caps,
    create_model_record,
    create_provider_record,
    delete_model_record,
    delete_provider_record,
    enqueue_disease_knowledge_repair_runs,
    enqueue_disease_knowledge_runs,
    enqueue_report_generation_task,
    fetch_call_logs_page,
    list_model_outputs,
    list_provider_outputs,
    runtime_route_to_out,
    update_model_record,
    update_provider_record,
)

router = APIRouter()


@router.post("/ai/disease-audit/run", response_model=Dict[str, Any])
async def run_disease_duplicate_audit(body: DiseaseDuplicateAuditRequest):
    if body.include_ai:
        return await run_worker_operation("disease_audit", body.model_dump())
    service = DiseaseDuplicateAuditService()
    return service.run_local_audit(
        include_new_disease_candidates=body.include_new_disease_candidates
    )


@router.get("/ai/disease-audit/status", response_model=Dict[str, Any])
async def get_disease_duplicate_audit_status(
    include_new_disease_candidates: bool = True,
):
    service = DiseaseDuplicateAuditService()
    return await service.status(
        include_new_disease_candidates=include_new_disease_candidates,
    )


@router.get("/ai/disease-audit/logs", response_model=List[Dict[str, Any]])
async def list_disease_duplicate_audit_logs(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    rows = DiseaseDuplicateAuditService.read_audit_logs(limit=page * page_size)
    offset = (page - 1) * page_size
    response.headers["X-Total-Count"] = str(len(rows))
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return rows[offset : offset + page_size]


@router.get("/knowledge", response_model=List[DiseaseKnowledgeCatalogueItem])
async def list_disease_knowledge_catalogue(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    service = DiseaseKnowledgeUpdateService()
    return await service.list_catalogue(db, search=search)


@router.post(
    "/knowledge/runs", response_model=DiseaseKnowledgeStartResponse, status_code=202
)
async def start_disease_knowledge_tasks(
    body: DiseaseKnowledgeStartRequest,
    db: AsyncSession = Depends(get_db),
):
    return await enqueue_disease_knowledge_runs(
        db,
        body,
        service=DiseaseKnowledgeUpdateService(),
        task_manager_ref=task_manager,
        ensure_schema=ensure_task_type_enum_schema,
    )


@router.post(
    "/knowledge/repair-runs",
    response_model=DiseaseKnowledgeRepairResponse,
    status_code=202,
)
async def start_disease_knowledge_repair_tasks(
    body: DiseaseKnowledgeRepairRequest,
    db: AsyncSession = Depends(get_db),
):
    return await enqueue_disease_knowledge_repair_runs(
        db,
        body,
        service=DiseaseKnowledgeUpdateService(),
    )


@router.post("/content-governance/run", response_model=AIContentGovernanceResponse)
async def run_ai_content_governance(
    body: AIContentGovernanceRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await run_worker_operation("content_governance", body.model_dump())
    return AIContentGovernanceResponse(**result)


@router.post("/content-governance/knowledge-retries")
async def requeue_failed_knowledge_repairs(
    body: KnowledgeRepairRetryRequest,
    db: AsyncSession = Depends(get_db),
):
    return await ai_content_governance_service.requeue_failed_knowledge_repairs(
        db,
        limit=body.limit,
    )


@router.get("/knowledge/{disease_id}", response_model=DiseaseKnowledgeDetail)
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


@router.post("/reports/runs", response_model=TaskOut, status_code=202)
async def start_ai_task(
    body: AIStartRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a GENERATE_REPORT task and enqueue it for external worker execution."""
    return await enqueue_report_generation_task(
        db, body, task_manager_ref=task_manager
    )


@router.get("/ai/models/providers", response_model=List[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    return await list_provider_outputs(db)


@router.post("/ai/models/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreateRequest, db: AsyncSession = Depends(get_db)
):
    await bootstrap_model_center_from_env(force=False)
    return await create_provider_record(db, body)


@router.put("/ai/models/providers/{provider_key}", response_model=ProviderOut)
async def update_provider(
    provider_key: str,
    body: ProviderUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    await bootstrap_model_center_from_env(force=False)
    return await update_provider_record(db, provider_key, body)


@router.post("/ai/models/providers/bootstrap")
async def bootstrap_providers(body: ProviderBootstrapRequest):
    await bootstrap_model_center_from_env(force=body.force)
    return {"ok": True, "force": body.force}


@router.delete("/ai/models/providers/{provider_key}")
async def delete_provider(provider_key: str, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    return await delete_provider_record(db, provider_key)


@router.post("/ai/models/providers/{provider_key}/test")
async def test_provider(
    provider_key: str,
    # A console test answers "is this route wired correctly"; the slower
    # structured workload probe stays opt-in and runs in background recovery.
    structured: bool = False,
    db: AsyncSession = Depends(get_db),
):
    await bootstrap_model_center_from_env(force=False)
    provider_id = (
        await db.execute(
            select(AIProviderConfig.id).where(
                AIProviderConfig.provider_key == provider_key
            )
        )
    ).scalar_one_or_none()
    if provider_id is None:
        raise HTTPException(404, "Provider not found")
    await db.rollback()
    return await run_worker_operation(
        "check_provider", {"provider_id": int(provider_id), "structured": structured}
    )


@router.post("/ai/models/providers/{provider_key}/discover-models")
async def discover_provider_models(
    provider_key: str, db: AsyncSession = Depends(get_db)
):
    """Read the provider API catalogue; discovered models are not auto-enabled."""
    await bootstrap_model_center_from_env(force=False)
    provider_id = (
        await db.execute(
            select(AIProviderConfig.id).where(
                AIProviderConfig.provider_key == provider_key
            )
        )
    ).scalar_one_or_none()
    if provider_id is None:
        raise HTTPException(404, "Provider not found")
    return await discover_provider_models_by_id(int(provider_id))


@router.get("/ai/models", response_model=List[ModelOut])
async def list_models(db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    return await list_model_outputs(db)


@router.post("/ai/models", response_model=ModelOut, status_code=201)
async def create_model(body: ModelCreateRequest, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    return await create_model_record(db, body)


@router.put("/ai/models/{model_key}", response_model=ModelOut)
async def update_model(
    model_key: str, body: ModelUpdateRequest, db: AsyncSession = Depends(get_db)
):
    await bootstrap_model_center_from_env(force=False)
    return await update_model_record(db, model_key, body)


@router.delete("/ai/models/{model_key}")
async def delete_model(model_key: str, db: AsyncSession = Depends(get_db)):
    await bootstrap_model_center_from_env(force=False)
    return await delete_model_record(db, model_key)


@router.post("/ai/models/{model_key}/test")
async def test_model(
    model_key: str,
    structured: bool = False,
    db: AsyncSession = Depends(get_db),
):
    await bootstrap_model_center_from_env(force=False)
    model_id = (
        await db.execute(
            select(AIModelConfig.id).where(AIModelConfig.model_key == model_key)
        )
    ).scalar_one_or_none()
    if model_id is None:
        raise HTTPException(404, "Model not found")
    await db.rollback()
    return await run_worker_operation(
        "check_model", {"model_id": int(model_id), "structured": structured}
    )


@router.post("/ai/models/check-all")
async def test_all_models(structured: bool = True):
    await bootstrap_model_center_from_env(force=False)
    return await run_worker_operation("check_all_models", {"structured": structured})


@router.get("/ai/models/call-logs", response_model=List[AIModelCallLogOut])
async def list_ai_model_call_logs(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    provider_key: Optional[str] = None,
    model_key: Optional[str] = None,
    status: Optional[str] = Query(None, pattern="^(success|failed)$"),
    db: AsyncSession = Depends(get_db),
):
    items, total, offset = await fetch_call_logs_page(
        db,
        page=page,
        page_size=page_size,
        provider_key=provider_key,
        model_key=model_key,
        status=status,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return items


@router.get("/ai/models/runtime", response_model=List[RuntimeRouteOut])
async def list_runtime_routes():
    routes = await get_runtime_routes()
    # Occupancy is shared by all dispatchers. The legacy "inflight" fields
    # report occupied durable permits, not a count of live HTTP connections.
    snapshot = await read_runtime_metrics(routes)
    routes = snapshot.apply_to_routes(routes)
    return [runtime_route_to_out(route) for route in routes]
