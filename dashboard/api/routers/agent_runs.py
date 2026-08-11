"""Agent workflow router."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.agent import (
    AgentWorkflowActionOut,
    AgentWorkflowCreateRequest,
    AgentWorkflowRunDetailOut,
    AgentWorkflowRunSummaryOut,
)
from src.core.task_manager import task_manager
from src.domain import AgentWorkflowRun, TaskStatus, TaskType, TaskPriority
from src.services.agent_workflow_service import agent_workflow_service
from src.control_plane.operations import CountryQueryRepository

router = APIRouter()


def _priority_from_text(value: str) -> TaskPriority:
    try:
        return TaskPriority(value.strip().lower())
    except Exception:
        return TaskPriority.NORMAL


def _not_found_or_conflict(exc: ValueError) -> HTTPException:
    message = str(exc)
    if "not found" in message.lower():
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=409, detail=message)


@router.post("/ai/runs", response_model=AgentWorkflowRunDetailOut, status_code=202)
async def create_agent_run(body: AgentWorkflowCreateRequest, db: AsyncSession = Depends(get_db)):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    country_id = await CountryQueryRepository(db).id_for_code(body.country_code)
    if body.country_code and country_id is None:
        raise HTTPException(404, "Country not found")

    task_name = body.task_name or f"Agent workflow: {prompt[:80]}"
    description = body.description or prompt
    input_data = {
        "prompt": prompt,
        "country_code": body.country_code.strip().upper() if body.country_code else None,
        "mode": body.mode,
        "output_format": body.output_format,
        "allowed_actions": body.allowed_actions,
        "memory_scope": body.memory_scope,
        "search_scope": body.search_scope,
        "priority": body.priority,
        "task_name": task_name,
        "description": description,
        "initiated_via": "dashboard",
        "requested_by": "agent-runs-api",
    }

    task = await task_manager.create_task(
        task_type=TaskType.AGENT_WORKFLOW,
        task_name=task_name,
        country_id=country_id,
        priority=_priority_from_text(body.priority),
        description=description,
        input_data=input_data,
    )

    run = (
        await db.execute(select(AgentWorkflowRun).where(AgentWorkflowRun.task_id == task.id))
    ).scalar_one_or_none()
    if run is None:
        run = AgentWorkflowRun(
            task_id=task.id,
            mode=body.mode,
            output_format=body.output_format,
            prompt=prompt,
            status="queued",
            risk_level="medium",
            country_id=country_id,
            search_scope=body.search_scope,
            memory_scope=body.memory_scope,
            allowed_actions=list(body.allowed_actions),
            plan_json=[],
            findings=[],
            citations=[],
            artifacts=[],
            open_questions=[],
            actions_taken=[],
            result_json={},
            budget_tokens_total=agent_workflow_service.total_token_budget,
            budget_tokens_used=0,
            replan_count=0,
            search_round_count=0,
            review_round_count=0,
            step_count=0,
            metadata_={
                "created_via": "dashboard",
                "allowed_actions": list(body.allowed_actions),
                "prompt": prompt,
            },
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            run = (
                await db.execute(select(AgentWorkflowRun).where(AgentWorkflowRun.task_id == task.id))
            ).scalar_one_or_none()
            if run is None:
                raise
    else:
        run.mode = body.mode
        run.output_format = body.output_format
        run.prompt = prompt
        run.status = run.status if run.status == "running" else "queued"
        run.country_id = country_id
        run.search_scope = body.search_scope
        run.memory_scope = body.memory_scope
        run.allowed_actions = list(body.allowed_actions)
        run.metadata_ = {**(run.metadata_ or {}), "created_via": "dashboard", "prompt": prompt}
        if run.status != "running":
            run.started_at = None
        await db.commit()

    current_task = await task_manager.get_task_by_uuid(task.task_uuid)
    queued_now = bool(current_task and current_task.status == TaskStatus.PENDING)
    current_status = getattr(current_task.status if current_task else task.status, "value", current_task.status if current_task else task.status)
    if queued_now:
        await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED)

    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Agent Workflow Queued" if queued_now else "Agent Workflow Registered",
            content=(
                f"Prompt: {prompt}\n"
                f"Scope: {body.search_scope}\n"
                f"Allowed actions: {', '.join(body.allowed_actions) or 'none'}\n"
                f"Task status: {current_status}"
            ),
            content_type="text",
            metadata={
                "task_uuid": task.task_uuid,
                "mode": body.mode,
                "output_format": body.output_format,
                "memory_scope": body.memory_scope,
                "search_scope": body.search_scope,
                "allowed_actions": body.allowed_actions,
                "queued_now": queued_now,
            },
        )
    except Exception as log_exc:
        from src.core import get_logger

        get_logger(__name__).warning("Failed to add agent workflow workbook entry for %s: %s", task.task_uuid, log_exc)

    try:
        return await agent_workflow_service.get_run_detail(task.task_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/ai/runs", response_model=list[AgentWorkflowRunSummaryOut])
async def list_agent_runs(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    db: AsyncSession = Depends(get_db),
):
    country_id = await CountryQueryRepository(db).id_for_code(country_code)
    if country_code and country_id is None:
        raise HTTPException(404, "Country not found")
    payload = await agent_workflow_service.list_runs(
        limit=page_size,
        offset=(page - 1) * page_size,
        status=status,
        search=search,
        country_id=country_id,
    )
    response.headers["X-Total-Count"] = str(payload["total"])
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str((page - 1) * page_size)
    return payload["items"]


@router.get("/ai/runs/{task_uuid}", response_model=AgentWorkflowRunDetailOut)
async def get_agent_run(task_uuid: str):
    try:
        return await agent_workflow_service.get_run_detail(task_uuid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/ai/runs/{task_uuid}/resume", response_model=AgentWorkflowActionOut, status_code=202)
async def resume_agent_run(task_uuid: str):
    try:
        await agent_workflow_service.resume_run(task_uuid)
        detail = await agent_workflow_service.get_run_detail(task_uuid)
        return AgentWorkflowActionOut(
            task_uuid=task_uuid,
            task_status=str(detail["task"]["status"]),
            run_status=str(detail["run"]["status"]),
            cancel_requested=False,
            message="resumed",
            detail=detail,
        )
    except ValueError as exc:
        raise _not_found_or_conflict(exc) from exc


@router.post("/ai/runs/{task_uuid}/cancel", response_model=AgentWorkflowActionOut, status_code=202)
async def cancel_agent_run(task_uuid: str):
    try:
        await agent_workflow_service.cancel_run(task_uuid)
        detail = await agent_workflow_service.get_run_detail(task_uuid)
        return AgentWorkflowActionOut(
            task_uuid=task_uuid,
            task_status=str(detail["task"]["status"]),
            run_status=str(detail["run"]["status"]),
            cancel_requested=True,
            message="cancel requested",
            detail=detail,
        )
    except ValueError as exc:
        raise _not_found_or_conflict(exc) from exc
