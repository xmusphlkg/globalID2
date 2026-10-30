"""Queue source-only knowledge collection through the existing worker service."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService

from ..deps import get_db
from ..schemas.control_plane import DataResponse, ResponseMeta
from ..schemas.knowledge_sources import (
    KnowledgeSourceRunCreate,
    KnowledgeSourceRunResult,
    KnowledgeSourceRunSkipped,
)
from ..schemas.operations import TaskReferenceOut

router = APIRouter(prefix="/knowledge")


@router.post(
    "/source-runs",
    response_model=DataResponse[KnowledgeSourceRunResult],
    status_code=202,
)
async def start_knowledge_source_runs(
    body: KnowledgeSourceRunCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    service = DiseaseKnowledgeUpdateService()
    catalogue = await service.list_catalogue(db)
    known = {str(item["disease_id"]).upper() for item in catalogue}
    unknown = [disease_id for disease_id in body.disease_ids if disease_id not in known]
    if unknown:
        raise HTTPException(422, f"Unknown disease IDs: {', '.join(unknown)}")

    result = await service.enqueue_source_refresh_tasks(
        db,
        disease_ids=body.disease_ids,
        source_groups=list(body.source),
        force=body.force,
        priority=body.priority,
        enqueue_ai_after_source_refresh=False,
        requested_by="collection-source-control",
        initiated_via="dashboard-knowledge-sources",
    )
    tasks = result["created_tasks"]
    references = [
        TaskReferenceOut(
            task_uuid=task.task_uuid,
            status=str(getattr(task.status, "value", task.status)),
            resource_id=task.task_uuid,
            href=f"/collection/runs/{task.task_uuid}",
        )
        for task in tasks
    ]
    skipped = [KnowledgeSourceRunSkipped.model_validate(item) for item in result["skipped"]]
    # The service deliberately excludes ineligible catalogue entries. Report
    # them explicitly rather than returning a silently empty accepted batch.
    accounted = {item.disease_id for item in skipped}
    accounted.update(
        str((task.input_data or {}).get("disease_id", "")).upper()
        for task in tasks
    )
    skipped.extend(
        KnowledgeSourceRunSkipped(disease_id=disease_id, reason="not_eligible")
        for disease_id in body.disease_ids
        if disease_id not in accounted
    )
    return DataResponse(
        data=KnowledgeSourceRunResult(
            requested_disease_ids=body.disease_ids,
            source_groups=result["source_groups"],
            force=body.force,
            created_count=len(references),
            skipped_count=len(skipped),
            created_tasks=references,
            skipped=skipped,
        ),
        meta=ResponseMeta(request_id=getattr(request.state, "request_id", None)),
    )
