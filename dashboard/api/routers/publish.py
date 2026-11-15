"""Publication reads committed scopes and enqueues releases; it never collects."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.collection import PublishDirty
from src.domain import DataReleaseJob
from src.core.config import get_config
from src.services.data_release_service import data_release_service
from src.services.data_release import publication_planning
from src.services.data_release import publication_execution

from ..deps import get_db
from ..schemas.collection import PendingOut, PublishRunCreate
from ..schemas.control_plane import DataResponse
from ..schemas.publication import (
    PublicationCapabilitiesOut, PublicationPlanCreate, PublicationPlanOut, PublicationUsageOut, PublicationRollbackPlanCreate,
)

DB_DEPENDENCY = Depends(get_db)

router = APIRouter(prefix="/publish")
ROOT_DIR = Path(__file__).resolve().parents[3]


@router.get("/capabilities", response_model=DataResponse[PublicationCapabilitiesOut])
async def capabilities():
    result = await asyncio.to_thread(publication_planning.capabilities, get_config().data_release)
    return DataResponse(data=PublicationCapabilitiesOut.model_validate(result))


@router.post("/plans", response_model=DataResponse[PublicationPlanOut])
async def plan(body: PublicationPlanCreate, db: AsyncSession = DB_DEPENDENCY):
    return await _create_plan(body, db=db)


async def _create_plan(body: PublicationPlanCreate, *, db, rollback_of=None):
    # Do not call load_jobs(): its initial storage setup may seed a job. This
    # endpoint only observes existing state and persists its private preview.
    job = (await db.execute(select(DataReleaseJob).where(DataReleaseJob.job_id == body.job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, {"code": "publication_job_not_found", "job_id": body.job_id})
    rows = (await db.execute(select(PublishDirty).where(PublishDirty.published_at.is_(None))
                             .order_by(PublishDirty.id))).scalars().all()
    frozen_rows = [{"id": row.id, "line": row.line, "scope_key": row.scope_key,
                    "change_set": row.change_set, "marked_at": row.marked_at} for row in rows]
    config = get_config()
    try:
        result = await asyncio.to_thread(
            publication_planning.create_plan, root=ROOT_DIR, data_dir=config.data_dir,
            job_id=body.job_id, config=config.data_release, dirty_rows=frozen_rows,
            job_configuration={column.name: getattr(job, column.name) for column in DataReleaseJob.__table__.columns},
            execution_service=data_release_service, requested_kind=body.kind, channel=body.channel, rollback_of=rollback_of,
            suppress_subscription_dispatch=body.suppress_subscription_dispatch,
        )
    except publication_execution.PublicationPlanError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc
    except ValueError as exc:
        raise HTTPException(409, {"code": "publication_preparation_failed"}) from exc
    return DataResponse(data=PublicationPlanOut.model_validate(result))


@router.post("/rollback-plans", response_model=DataResponse[PublicationPlanOut])
async def rollback_plan(body: PublicationRollbackPlanCreate, db: AsyncSession = DB_DEPENDENCY):
    return await _create_plan(PublicationPlanCreate(job_id=body.job_id, kind="theme"), db=db,
                              rollback_of=body.target_plan_id)


@router.get("/theme-history", response_model=DataResponse[dict])
async def theme_history():
    return DataResponse(data=await asyncio.to_thread(publication_execution.theme_history, get_config().data_dir))


async def _preview_directory(plan_id):
    try:
        await asyncio.to_thread(publication_execution.load_plan, get_config().data_dir, plan_id)
    except publication_execution.PublicationPlanError as exc:
        raise HTTPException(exc.status, {"code": exc.code}) from exc
    return get_config().data_dir / "publication-plans" / plan_id


@router.get("/plans/{plan_id}/previews", response_model=DataResponse[dict])
async def previews(plan_id: str):
    import json
    directory = await _preview_directory(plan_id)
    try:
        index = json.loads((directory / "theme-previews.json").read_bytes())
    except (OSError, ValueError):
        return DataResponse(data={"pages": [], "missing": ["preview_not_prepared"]})
    return DataResponse(data={"pages": list(index["pages"]), "missing": index.get("missing", [])})


@router.get("/plans/{plan_id}/receipt", response_model=DataResponse[dict])
async def receipt(plan_id: str, db: AsyncSession = DB_DEPENDENCY):
    import json
    from src.domain import Task, TaskStatus
    directory = await _preview_directory(plan_id)
    task = (await db.execute(select(Task).where(
        Task.input_data["publication_plan"]["plan_id"].as_string() == plan_id,
    ).order_by(Task.created_at.desc()).limit(1))).scalar_one_or_none()
    try:
        value = json.loads((directory / "theme-receipt.json").read_bytes())
    except (OSError, ValueError):
        result = {"plan_id": plan_id, "status": task.status.value if task else "not_queued",
                  "task_uuid": task.task_uuid if task else None}
        checkpoints = (task.metadata_ or {}).get("release_checkpoints", {}) if task else {}
        result["authoritative_committed"] = bool(checkpoints.get("cloudflare_deploy")) or None
        result.update({key: val for key, val in checkpoints.get("public_verification", {}).items()
                       if key in {"authoritative_committed", "public_verified", "public_verified_at"}})
        result.update({key: val for key, val in checkpoints.get("country_template", {}).items()
                       if key in {"content_render_count", "html_assembly_count", "template_compile_count", "papers_render_count",
                                  "reports_render_count", "compile_seconds", "assembly_seconds", "elapsed_seconds"}})
        if checkpoints.get("country_template", {}).get("elapsed_seconds") is not None:
            result["duration_seconds"] = checkpoints["country_template"]["elapsed_seconds"]
        if task and (task.input_data or {}).get("data_line") == "literature":
            result.update(await asyncio.to_thread(publication_execution.literature_receipt, ROOT_DIR, task.task_uuid))
        result["database_confirmed"] = bool(task and task.status == TaskStatus.COMPLETED)
        result.setdefault("public_verified", None)
        return DataResponse(data=result)
    allowed = {"plan_id", "version", "compatibility", "content_render_count", "html_assembly_count",
               "template_compile_count", "theme_compile_count", "pages_deployment_required", "uploaded_objects",
               "uploaded_bytes", "authoritative_committed", "committed_at", "public_verified",
               "public_verified_at", "duration_seconds"}
    result = {key: val for key, val in value.items() if key in allowed}
    result["database_confirmed"] = bool(task and task.status == TaskStatus.COMPLETED)
    result["task_uuid"] = task.task_uuid if task else None
    result["status"] = task.status.value if task else "not_queued"
    return DataResponse(data=result)


@router.get("/plans/{plan_id}/preview/{locale}/{family}")
async def preview(plan_id: str, locale: str, family: str):
    from src.services.data_release.theme_preview import read_theme_preview
    directory = await _preview_directory(plan_id)
    try:
        body, headers = await asyncio.to_thread(read_theme_preview, directory, locale, family)
    except (OSError, ValueError, KeyError):
        raise HTTPException(404, {"code": "publication_preview_unavailable"}) from None
    return Response(body, media_type="text/html", headers=headers)


@router.get("/usage", response_model=DataResponse[PublicationUsageOut])
async def usage():
    result = await asyncio.to_thread(publication_planning.read_usage,
                                    get_config().data_dir / "cloudflare-publication-usage.json")
    return DataResponse(data=PublicationUsageOut.model_validate(result))


@router.get("/pending", response_model=DataResponse[PendingOut])
async def pending(db: AsyncSession = DB_DEPENDENCY):
    config = get_config().data_release
    rows = (
        await db.execute(
            select(PublishDirty.line, PublishDirty.scope_key)
            .where(PublishDirty.published_at.is_(None))
            .distinct()
            .order_by(PublishDirty.line, PublishDirty.scope_key)
        )
    ).all()
    groups = {
        line: [scope for item, scope in rows if item == line]
        for line in ("surveillance", "literature", "situation", "knowledge")
    }
    return DataResponse(
        data=PendingOut(
            surveillance=groups["surveillance"],
            literature=bool(groups["literature"]),
            situation=bool(groups["situation"]),
            knowledge=groups["knowledge"],
            surveillance_json_enabled=bool(config.surveillance_json_enabled),
            situation_json_enabled=bool(config.situation_json_enabled),
            literature_json_enabled=bool(getattr(config, "literature_json_enabled", False)),
        )
    )


@router.post("/runs", response_model=DataResponse[dict], status_code=202)
async def start(body: PublishRunCreate, db: AsyncSession = DB_DEPENDENCY):
    if body.plan_id:
        try:
            result = await publication_execution.enqueue_plan(body, db=db, root=ROOT_DIR,
                data_dir=get_config().data_dir, service=data_release_service)
        except publication_execution.PublicationPlanError as exc:
            raise HTTPException(exc.status, {"code": exc.code}) from exc
        return DataResponse(data=result)
    if sum((body.full, body.reports_only, body.code_only)) > 1:
        raise HTTPException(400, "Full, reports-only and code-only release modes are mutually exclusive")
    try:
        result = await data_release_service.trigger_job(
            body.job_id, manual=True, trigger="dashboard", full=body.full,
            reports_only=body.reports_only, code_only=body.code_only,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return DataResponse(data=result)
