"""Persistence helpers and state transitions for agent workflow runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from src.domain import (
    AgentWorkflowConversation,
    AgentWorkflowRun,
    AgentWorkflowStep,
    Country,
    Task,
)
from src.services.agent_workflow_types import EvidenceRef, PlanNode


async def load_run_row(db: Any, task_uuid: str) -> Any:
    query = (
        select(AgentWorkflowRun, Task, Country.code.label("country_code"), Country.name_en.label("country_name"))
        .join(Task, Task.id == AgentWorkflowRun.task_id)
        .outerjoin(Country, Country.id == AgentWorkflowRun.country_id)
        .where(Task.task_uuid == task_uuid)
    )
    return (await db.execute(query)).one_or_none()


async def get_or_create_run(
    db: Any,
    task: Task,
    *,
    prompt: str,
    mode: str,
    output_format: str,
    country_id: Optional[int],
    search_scope: str,
    memory_scope: str,
    allowed_actions: set[str],
    payload: dict[str, Any],
    total_token_budget: int,
    now: Callable[[], datetime],
) -> AgentWorkflowRun:
    existing = (
        await db.execute(select(AgentWorkflowRun).where(AgentWorkflowRun.task_id == task.id))
    ).scalar_one_or_none()
    if existing is not None:
        existing.prompt = prompt
        existing.mode = mode
        existing.output_format = output_format
        existing.country_id = country_id
        existing.search_scope = search_scope
        existing.memory_scope = memory_scope
        existing.allowed_actions = sorted(allowed_actions)
        existing.metadata_ = {**(existing.metadata_ or {}), **payload}
        if existing.status not in {"completed", "failed", "cancelled"}:
            existing.status = "running"
            if existing.started_at is None:
                existing.started_at = now()
        await db.flush()
        return existing

    run = AgentWorkflowRun(
        task_id=task.id,
        mode=mode,
        output_format=output_format,
        prompt=prompt,
        status="running",
        risk_level="medium",
        country_id=country_id,
        search_scope=search_scope,
        memory_scope=memory_scope,
        allowed_actions=sorted(allowed_actions),
        plan_json=[],
        findings=[],
        citations=[],
        artifacts=[],
        open_questions=[],
        actions_taken=[],
        result_json={},
        budget_tokens_total=total_token_budget,
        budget_tokens_used=0,
        replan_count=0,
        search_round_count=0,
        review_round_count=0,
        step_count=0,
        metadata_=payload,
        started_at=now(),
    )
    db.add(run)
    await db.flush()
    return run


async def load_completed_steps(db: Any, run_id: int) -> dict[str, AgentWorkflowStep]:
    rows = (
        await db.execute(
            select(AgentWorkflowStep)
            .where(AgentWorkflowStep.run_id == run_id)
            .order_by(AgentWorkflowStep.step_order.asc())
        )
    ).scalars().all()
    return {row.step_key: row for row in rows if str(row.status) == "completed"}


async def start_step(
    db: Any,
    run: AgentWorkflowRun,
    node: PlanNode,
    *,
    now: Callable[[], datetime],
) -> AgentWorkflowStep:
    step = (
        await db.execute(
            select(AgentWorkflowStep).where(
                AgentWorkflowStep.run_id == run.id,
                AgentWorkflowStep.step_key == node.step_key,
            )
        )
    ).scalar_one_or_none()
    if step is None:
        step = AgentWorkflowStep(
            run_id=run.id,
            step_key=node.step_key,
            step_order=len(run.steps) + 1,
            step_type=node.step_type,
            step_name=node.title,
            status="running",
            attempt=1,
            input_payload=node.to_dict(),
            metadata_={"step_type": node.step_type},
            started_at=now(),
        )
        db.add(step)
        await db.flush()
        return step

    step.status = "running"
    step.attempt = int(step.attempt or 0) + 1
    step.input_payload = node.to_dict()
    step.started_at = now()
    step.ended_at = None
    step.error_message = None
    await db.flush()
    return step


async def finish_step(
    db: Any,
    run: AgentWorkflowRun,
    step: AgentWorkflowStep,
    result: dict[str, Any],
    *,
    now: Callable[[], datetime],
    parse_datetime: Callable[[Any], Optional[datetime]],
    upsert_evidence: Callable[[Any, AgentWorkflowRun, AgentWorkflowStep, EvidenceRef], Awaitable[None]],
) -> None:
    step.status = "completed"
    step.output_payload = result.get("output_payload") or {}
    step.output_summary = result.get("output_summary")
    step.prompt = result.get("prompt")
    step.system_prompt = result.get("system_prompt")
    step.response = result.get("response")
    step.model = result.get("model")
    step.provider = result.get("provider")
    step.tokens = result.get("tokens") or {}
    step.duration = result.get("duration")
    step.ended_at = now()
    step.metadata_ = {
        **(step.metadata_ or {}),
        "evidence_count": len(result.get("evidence", [])),
    }
    await db.flush()

    for evidence in result.get("evidence", []):
        await upsert_evidence(db, run, step, evidence)

    for conversation in result.get("conversations", []):
        db.add(
            AgentWorkflowConversation(
                run_id=run.id,
                step_id=step.id,
                agent_role=str(conversation.get("agent_role") or step.step_type),
                phase=str(conversation.get("phase") or step.step_type),
                timestamp=parse_datetime(conversation.get("timestamp")) or now(),
                prompt=conversation.get("prompt"),
                system_prompt=conversation.get("system_prompt"),
                response=conversation.get("response"),
                model=conversation.get("model"),
                provider=conversation.get("provider"),
                tokens=conversation.get("tokens") or {},
                duration=conversation.get("duration"),
                temperature=conversation.get("temperature"),
                metadata_=conversation.get("metadata") or {},
            )
        )
    await db.flush()


async def fail_step(
    db: Any,
    run: AgentWorkflowRun,
    step: AgentWorkflowStep,
    exc: Exception,
    *,
    now: Callable[[], datetime],
) -> None:
    step.status = "failed"
    step.error_message = str(exc)
    step.ended_at = now()
    run.status = "failed"
    run.error_message = str(exc)
    run.ended_at = now()
    await db.flush()
