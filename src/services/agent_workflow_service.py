"""Generic multi-expert workflow service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_, select

from src.ai.agents import WorkflowAgent
from src.core import get_config, get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    AgentWorkflowConversation,
    AgentWorkflowEvidence,
    AgentWorkflowMemory,
    AgentWorkflowRun,
    AgentWorkflowStep,
    Country,
    CrawlRun,
    Disease,
    DiseaseMapping,
    DiseaseRecord,
    Report,
    ReportSection,
    StandardDisease,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
    TaskWorkbook,
)
from src.generation import DataExporter
from src.services.agent_workflow_types import ActionRequest, ActionResult, AgentFinalResult, EvidenceRef, PlanNode
from src.services.crawl_service import CrawlService
from src.services.data_release_service import data_release_service
from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService
from src.services.exceptions import TaskCancelledError
from src.services.report_service import ReportService

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWED_ACTIONS = {"crawl_data", "generate_report", "update_disease_knowledge", "export_data"}
WEB_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
WEB_USER_AGENT = "GlobalID-AgentWorkflow/1.0 (+https://globalid.local)"
QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "about",
    "for",
    "from",
    "how",
    "is",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "please",
    "need",
    "use",
    "task",
    "prompt",
    "data",
    "search",
    "analyze",
    "analysis",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _stable_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _safe_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    return value


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _extract_keywords(prompt: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", prompt.lower())
    keywords: list[str] = []
    for token in tokens:
        if token in QUERY_STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class AgentWorkflowService:
    """Orchestrates the generic research-oriented multi-expert workflow."""

    def __init__(self) -> None:
        self.config = get_config()
        self.role_models = self.config.ai.agent_role_models
        self.max_replan_rounds = int(self.config.ai.agent_max_replan_rounds)
        self.max_search_rounds = int(self.config.ai.agent_max_search_rounds)
        self.step_token_budget = int(self.config.ai.agent_step_token_budget)
        self.total_token_budget = int(self.config.ai.agent_total_token_budget)
        self.web_session = requests.Session()
        self.web_session.headers.update({"User-Agent": WEB_USER_AGENT})
        self._exporter = DataExporter()
        self._memory_client_checked = False
        self._memory_client = None

    async def execute(self, task: Task) -> dict[str, Any]:
        """Execute a top-level AGENT_WORKFLOW task."""
        payload = dict(task.input_data or {})
        prompt = str(payload.get("prompt") or task.description or task.task_name or "").strip()
        if not prompt:
            raise ValueError("AGENT_WORKFLOW tasks require a prompt")

        allowed_actions = self._normalize_actions(payload.get("allowed_actions"))
        search_scope = str(payload.get("search_scope") or "web+db+memory")
        memory_scope = str(payload.get("memory_scope") or "project")
        output_format = str(payload.get("output_format") or "evidence_report")
        country_id = self._coerce_int(payload.get("country_id") or task.country_id)
        mode = str(payload.get("mode") or "research")

        async with get_database() as db:
            run: Optional[AgentWorkflowRun] = None
            final_output: Optional[AgentFinalResult] = None
            try:
                run = await self._get_or_create_run(
                    db,
                    task,
                    prompt=prompt,
                    mode=mode,
                    output_format=output_format,
                    country_id=country_id,
                    search_scope=search_scope,
                    memory_scope=memory_scope,
                    allowed_actions=allowed_actions,
                    payload=payload,
                )
                if run.status == "completed" and isinstance(run.result_json, dict) and run.result_json:
                    return dict(run.result_json)

                try:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title="Agent Workflow Started",
                        content=(
                            f"Prompt: {_compact_text(prompt, 800)}\n"
                            f"Scope: {search_scope}\n"
                            f"Allowed actions: {', '.join(sorted(allowed_actions)) or 'none'}"
                        ),
                        content_type="text",
                        metadata={
                            "task_uuid": task.task_uuid,
                            "mode": mode,
                            "search_scope": search_scope,
                            "memory_scope": memory_scope,
                            "allowed_actions": sorted(allowed_actions),
                        },
                    )
                except Exception as log_exc:
                    logger.warning("Failed to add workflow start log for %s: %s", task.task_uuid, log_exc)

                plan_nodes = await self._ensure_plan(db, run, task, prompt, payload, search_scope, allowed_actions)
                completed_steps = await self._load_completed_steps(db, run.id)
                context = self._build_initial_context(
                    prompt=prompt,
                    payload=payload,
                    search_scope=search_scope,
                    memory_scope=memory_scope,
                )
                context["plan"] = [node.to_dict() for node in plan_nodes]
                await db.commit()

                i = 0
                while i < len(plan_nodes):
                    if await task_manager.is_cancel_requested(task.task_uuid):
                        raise TaskCancelledError("Cancellation requested by user")

                    node = plan_nodes[i]
                    step = completed_steps.get(node.step_key)
                    if step and str(step.status) == "completed":
                        context = self._merge_step_context(context, step.output_payload or {}, step.step_type)
                        i += 1
                        continue

                    step = await self._start_step(db, run, node)
                    try:
                        result = await self._execute_node(
                            db=db,
                            task=task,
                            run=run,
                            node=node,
                            context=context,
                            allowed_actions=allowed_actions,
                            prompt=prompt,
                        )
                        await self._finish_step(db, run, step, result)
                        await db.commit()
                        completed_steps[node.step_key] = step
                        context = self._merge_step_context(context, result["output_payload"], node.step_type)
                        await self._refresh_run_progress(db, run, task, completed_steps_count=len(completed_steps))
                        await db.commit()

                        if node.step_type == "review" and not result["output_payload"].get("approved", False):
                            follow_up_nodes = self._maybe_schedule_replan_nodes(
                                run=run,
                                node=node,
                                result=result["output_payload"],
                                context=context,
                            )
                            if follow_up_nodes:
                                plan_nodes.extend(follow_up_nodes)
                                context["plan"] = [item.to_dict() for item in plan_nodes]
                                await self._persist_plan(db, run, plan_nodes)
                                await db.commit()

                        if node.step_type == "finalize":
                            break
                    except Exception as exc:
                        await self._fail_step(db, run, step, exc)
                        await db.commit()
                        raise

                    i += 1

                final_output = await self._build_final_output(db, run, task, prompt, context, plan_nodes)
                run.status = "completed"
                run.summary = final_output.summary
                run.findings = final_output.findings
                run.citations = final_output.citations
                run.actions_taken = final_output.actions_taken
                run.artifacts = final_output.artifacts
                run.open_questions = final_output.open_questions
                run.result_json = final_output.to_dict()
                run.step_count = len([step for step in completed_steps.values() if str(step.status) == "completed"])
                run.budget_tokens_total = self.total_token_budget
                run.budget_tokens_used = self._aggregate_tokens([step.tokens for step in completed_steps.values()])
                run.ended_at = _now()
                run.metadata_ = {
                    **(run.metadata_ or {}),
                    "task_uuid": task.task_uuid,
                    "prompt_hash": _stable_hash(prompt),
                    "search_scope": search_scope,
                    "memory_scope": memory_scope,
                    "allowed_actions": sorted(allowed_actions),
                    "plan_steps": len(plan_nodes),
                    "step_count": run.step_count,
                }
                await db.commit()

                try:
                    await self._store_workflow_memory(db, run, task, prompt, final_output)
                    await db.commit()
                except Exception as memory_exc:
                    logger.warning("Workflow memory persistence skipped for %s: %s", task.task_uuid, memory_exc)
            except TaskCancelledError as exc:
                if run is not None:
                    run.status = "cancelled"
                    run.error_message = str(exc)
                    run.ended_at = _now()
                    run.metadata_ = {**(run.metadata_ or {}), "cancelled": True, "cancel_reason": str(exc)}
                    await db.commit()
                try:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="warning",
                        title="Agent Workflow Cancelled",
                        content=str(exc),
                        content_type="text",
                        metadata={"task_uuid": task.task_uuid, "cancelled": True},
                    )
                except Exception as log_exc:
                    logger.warning("Failed to add workflow cancellation log for %s: %s", task.task_uuid, log_exc)
                raise
            except Exception as exc:
                if run is not None:
                    run.status = "failed"
                    run.error_message = str(exc)
                    run.ended_at = _now()
                    run.metadata_ = {**(run.metadata_ or {}), "failed": True, "error": str(exc)}
                    await db.commit()
                try:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="error",
                        title="Agent Workflow Failed",
                        content=str(exc),
                        content_type="text",
                        metadata={"task_uuid": task.task_uuid, "failed": True},
                    )
                except Exception as log_exc:
                    logger.warning("Failed to add workflow failure log for %s: %s", task.task_uuid, log_exc)
                raise

        try:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Agent Workflow Completed",
                content=_compact_text(json.dumps(final_output.to_dict() if final_output else {}, ensure_ascii=False), 1500),
                content_type="json",
                metadata={
                    "task_uuid": task.task_uuid,
                    "evidence_count": final_output.evidence_count if final_output else 0,
                    "step_count": final_output.step_count if final_output else 0,
                    "risk_level": final_output.risk_level if final_output else "medium",
                },
            )
        except Exception as log_exc:
            logger.warning("Failed to add workflow completion log for %s: %s", task.task_uuid, log_exc)

        return final_output.to_dict() if final_output else {}

    async def list_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        search: Optional[str] = None,
        country_id: Optional[int] = None,
    ) -> dict[str, Any]:
        async with get_database() as db:
            query = (
                select(AgentWorkflowRun, Task, Country.code.label("country_code"), Country.name_en.label("country_name"))
                .join(Task, Task.id == AgentWorkflowRun.task_id)
                .outerjoin(Country, Country.id == AgentWorkflowRun.country_id)
                .order_by(AgentWorkflowRun.created_at.desc())
            )
            filters = []
            if status:
                filters.append(AgentWorkflowRun.status == status)
            if country_id is not None:
                filters.append(AgentWorkflowRun.country_id == country_id)
            if search:
                like = f"%{search}%"
                filters.append(
                    or_(
                        Task.task_name.ilike(like),
                        Task.task_uuid.ilike(like),
                        AgentWorkflowRun.prompt.ilike(like),
                        AgentWorkflowRun.summary.ilike(like),
                    )
                )
                if filters:
                    query = query.where(*filters)

            from sqlalchemy import func

            count_q = select(func.count()).select_from(AgentWorkflowRun).join(Task, Task.id == AgentWorkflowRun.task_id)
            if filters:
                count_q = count_q.where(*filters)
            total = int((await db.execute(count_q)).scalar_one() or 0)

            rows = (await db.execute(query.offset(offset).limit(limit))).all()
            items = [self._serialize_run_row(row) for row in rows]
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": items,
            }

    async def get_run_detail(self, task_uuid: str) -> dict[str, Any]:
        async with get_database() as db:
            row = await self._load_run_row(db, task_uuid)
            if row is None:
                raise ValueError(f"Agent workflow run not found for task_uuid={task_uuid}")
            run = row.AgentWorkflowRun
            task = row.Task
            steps = (
                await db.execute(
                    select(AgentWorkflowStep).where(AgentWorkflowStep.run_id == run.id).order_by(AgentWorkflowStep.step_order.asc())
                )
            ).scalars().all()
            workbook_entries = (
                await db.execute(
                    select(TaskWorkbook).where(TaskWorkbook.task_id == task.id).order_by(TaskWorkbook.created_at.asc())
                )
            ).scalars().all()
            evidence_items = (
                await db.execute(
                    select(AgentWorkflowEvidence).where(AgentWorkflowEvidence.run_id == run.id).order_by(AgentWorkflowEvidence.created_at.asc())
                )
            ).scalars().all()
            conversations = (
                await db.execute(
                    select(AgentWorkflowConversation).where(AgentWorkflowConversation.run_id == run.id).order_by(AgentWorkflowConversation.timestamp.asc())
                )
            ).scalars().all()
            memories = (
                await db.execute(
                    select(AgentWorkflowMemory).where(AgentWorkflowMemory.run_id == run.id).order_by(AgentWorkflowMemory.created_at.asc())
                )
            ).scalars().all()
            task_payload = self._serialize_task(task)
            task_payload["workbook_entries"] = [self._serialize_task_workbook_entry(entry) for entry in workbook_entries]
            return {
                "task": task_payload,
                "run": self._serialize_run(run),
                "steps": [self._serialize_step(step) for step in steps],
                "evidence": [self._serialize_evidence(item) for item in evidence_items],
                "conversations": [self._serialize_conversation(item) for item in conversations],
                "memories": [self._serialize_memory(item) for item in memories],
            }

    async def cancel_run(self, task_uuid: str, reason: str = "Cancelled by user") -> dict[str, Any]:
        task = await task_manager.request_task_cancel(task_uuid, reason=reason)
        if task is None:
            raise ValueError(f"Task not found: {task_uuid}")
        if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            raise ValueError(f"Task {task_uuid} is not cancellable from status {task.status}")
        async with get_database() as db:
            row = await self._load_run_row(db, task_uuid)
            if row is not None:
                run = row.AgentWorkflowRun
                if run.status not in {"completed", "failed", "cancelled"}:
                    run.status = "cancelled"
                    run.error_message = reason
                    run.ended_at = _now()
                    run.metadata_ = {**(run.metadata_ or {}), "cancel_requested": True, "cancel_reason": reason}
                    await db.commit()
        return {"task_uuid": task.task_uuid, "status": str(task.status), "cancel_requested": True}

    async def resume_run(self, task_uuid: str) -> dict[str, Any]:
        task = await task_manager.get_task_by_uuid(task_uuid)
        if task is None:
            raise ValueError(f"Task not found: {task_uuid}")
        if task.status not in [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.PENDING, TaskStatus.QUEUED]:
            raise ValueError(f"Task {task_uuid} is not resumable from status {task.status}")
        await task_manager.clear_task_cancel_request(task_uuid)
        await task_manager.update_task_status(task_uuid, TaskStatus.QUEUED)
        async with get_database() as db:
            row = await self._load_run_row(db, task_uuid)
            if row is not None:
                run = row.AgentWorkflowRun
                if run.status != "completed":
                    run.status = "queued"
                    run.error_message = None
                    run.ended_at = None
                    run.metadata_ = {**(run.metadata_ or {}), "cancel_requested": False, "resumed": True}
                    await db.commit()
        return {"task_uuid": task.task_uuid, "status": "queued"}

    async def _get_or_create_run(
        self,
        db,
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
                    existing.started_at = _now()
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
            budget_tokens_total=self.total_token_budget,
            budget_tokens_used=0,
            replan_count=0,
            search_round_count=0,
            review_round_count=0,
            step_count=0,
            metadata_=payload,
            started_at=_now(),
        )
        db.add(run)
        await db.flush()
        return run

    async def _ensure_plan(
        self,
        db,
        run: AgentWorkflowRun,
        task: Task,
        prompt: str,
        payload: dict[str, Any],
        search_scope: str,
        allowed_actions: set[str],
    ) -> list[PlanNode]:
        if run.plan_json:
            try:
                return [self._plan_node_from_dict(item) for item in list(run.plan_json)]
            except Exception:
                logger.warning("Existing plan payload for run %s could not be parsed; replanning", run.id)

        planner = WorkflowAgent(
            name="Planner",
            system_prompt=self._planner_system_prompt(),
            temperature=0.1,
            max_tokens=min(self.step_token_budget, 3000),
        )
        planner.clear_conversation_history()
        result = await planner.process(
            prompt=self._planner_prompt(prompt=prompt, task=task, payload=payload, search_scope=search_scope, allowed_actions=allowed_actions),
            preferred_models=self._role_models_for("planner"),
        )
        nodes = self._parse_plan_nodes(result)
        if not nodes:
            nodes = self._default_plan(prompt, search_scope, allowed_actions)
            result = {"risk_level": "medium", "plan": [node.to_dict() for node in nodes], "summary": "Fallback deterministic plan"}

        run.risk_level = str(result.get("risk_level") or "medium")
        await self._persist_plan(db, run, nodes)
        await self._persist_agent_conversation(db, run, None, "planner", "planning", planner)
        try:
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Agent Plan Created",
                content=_compact_text(json.dumps([node.to_dict() for node in nodes], ensure_ascii=False), 1800),
                content_type="json",
                metadata={"task_uuid": task.task_uuid, "risk_level": run.risk_level, "node_count": len(nodes)},
            )
        except Exception as log_exc:
            logger.warning("Failed to add workflow plan log for %s: %s", task.task_uuid, log_exc)
        await db.commit()
        return nodes

    async def _persist_plan(self, db, run: AgentWorkflowRun, nodes: list[PlanNode]) -> None:
        run.plan_json = [node.to_dict() for node in nodes]
        run.metadata_ = {**(run.metadata_ or {}), "plan_steps": len(nodes)}
        await db.flush()

    async def _load_completed_steps(self, db, run_id: int) -> dict[str, AgentWorkflowStep]:
        rows = (
            await db.execute(
                select(AgentWorkflowStep).where(AgentWorkflowStep.run_id == run_id).order_by(AgentWorkflowStep.step_order.asc())
            )
        ).scalars().all()
        return {row.step_key: row for row in rows if str(row.status) == "completed"}

    async def _start_step(self, db, run: AgentWorkflowRun, node: PlanNode) -> AgentWorkflowStep:
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
                started_at=_now(),
            )
            db.add(step)
            await db.flush()
            return step

        step.status = "running"
        step.attempt = int(step.attempt or 0) + 1
        step.input_payload = node.to_dict()
        step.started_at = _now()
        step.ended_at = None
        step.error_message = None
        await db.flush()
        return step

    async def _finish_step(self, db, run: AgentWorkflowRun, step: AgentWorkflowStep, result: dict[str, Any]) -> None:
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
        step.ended_at = _now()
        step.metadata_ = {
            **(step.metadata_ or {}),
            "evidence_count": len(result.get("evidence", [])),
        }
        await db.flush()

        for evidence in result.get("evidence", []):
            await self._upsert_evidence(db, run, step, evidence)

        for conversation in result.get("conversations", []):
            db.add(
                AgentWorkflowConversation(
                    run_id=run.id,
                    step_id=step.id,
                    agent_role=str(conversation.get("agent_role") or step.step_type),
                    phase=str(conversation.get("phase") or step.step_type),
                    timestamp=self._parse_datetime(conversation.get("timestamp")) or _now(),
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

    async def _fail_step(self, db, run: AgentWorkflowRun, step: AgentWorkflowStep, exc: Exception) -> None:
        step.status = "failed"
        step.error_message = str(exc)
        step.ended_at = _now()
        run.status = "failed"
        run.error_message = str(exc)
        run.ended_at = _now()
        await db.flush()

    async def _execute_node(
        self,
        *,
        db,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        allowed_actions: set[str],
        prompt: str,
    ) -> dict[str, Any]:
        if node.step_type == "web_search":
            return await self._run_web_search(task=task, run=run, node=node, context=context, prompt=prompt)
        if node.step_type == "db_lookup":
            return await self._run_db_lookup(task=task, run=run, node=node, context=context, prompt=prompt)
        if node.step_type == "memory_lookup":
            return await self._run_memory_lookup(db=db, task=task, run=run, node=node, context=context, prompt=prompt)
        if node.step_type == "analysis":
            return await self._run_analysis(task=task, run=run, node=node, context=context, prompt=prompt)
        if node.step_type == "internal_action":
            return await self._run_internal_action(db=db, task=task, run=run, node=node, context=context, prompt=prompt, allowed_actions=allowed_actions)
        if node.step_type == "review":
            return await self._run_review(task=task, run=run, node=node, context=context, prompt=prompt)
        if node.step_type == "finalize":
            return await self._run_finalize(db=db, task=task, run=run, node=node, context=context, prompt=prompt)
        raise ValueError(f"Unsupported plan node type: {node.step_type}")

    async def _run_web_search(
        self,
        *,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        queries = self._build_search_queries(node.search_queries or [prompt], prompt=prompt, max_rounds=self.max_search_rounds)
        evidence: list[EvidenceRef] = []
        results: list[dict[str, Any]] = []
        for query in queries[: self.max_search_rounds]:
            hits = await asyncio.to_thread(self._duckduckgo_search, query, node.max_results)
            for hit in hits:
                evidence.append(hit)
                results.append(hit.to_dict())
        run.search_round_count += 1
        summary = self._summarize_evidence(evidence, title="Web search")
        return {
            "output_payload": {"queries": queries, "results": results, "summary": summary},
            "output_summary": summary,
            "evidence": evidence,
            "conversations": [],
            "tokens": {},
            "duration": 0.0,
            "model": None,
            "provider": None,
            "response": None,
            "prompt": None,
            "system_prompt": None,
        }

    async def _run_db_lookup(
        self,
        *,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        terms = node.search_queries or self._build_search_queries([prompt], prompt=prompt, max_rounds=1)
        table_names = node.target_tables or list(self._db_search_registry().keys())
        evidence: list[EvidenceRef] = []
        results: list[dict[str, Any]] = []
        async with get_database() as db:
            for table_name in table_names:
                rows = await self._search_database_table(db, table_name, terms, limit=node.max_results)
                for row in rows:
                    evidence.append(row)
                    results.append(row.to_dict())
        summary = self._summarize_evidence(evidence, title="Database lookup")
        return {
            "output_payload": {"tables": table_names, "results": results, "summary": summary},
            "output_summary": summary,
            "evidence": evidence,
            "conversations": [],
            "tokens": {},
            "duration": 0.0,
            "model": None,
            "provider": None,
            "response": None,
            "prompt": None,
            "system_prompt": None,
        }

    async def _run_memory_lookup(
        self,
        *,
        db,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        terms = node.search_queries or self._build_search_queries([prompt], prompt=prompt, max_rounds=1)
        evidence: list[EvidenceRef] = []
        async with get_database() as lookup_db:
            memories = await self._search_memory(lookup_db, run, prompt=prompt, terms=terms, limit=node.max_results)
            evidence.extend(memories)
        summary = self._summarize_evidence(evidence, title="Memory lookup")
        return {
            "output_payload": {"queries": terms, "results": [item.to_dict() for item in evidence], "summary": summary},
            "output_summary": summary,
            "evidence": evidence,
            "conversations": [],
            "tokens": {},
            "duration": 0.0,
            "model": None,
            "provider": None,
            "response": None,
            "prompt": None,
            "system_prompt": None,
        }

    async def _run_analysis(
        self,
        *,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        agent = WorkflowAgent(
            name="Analyst",
            system_prompt=self._analysis_system_prompt(),
            temperature=0.2,
            max_tokens=min(self.step_token_budget, 3000),
        )
        agent.clear_conversation_history()
        analysis_payload = {
            "prompt": prompt,
            "search_scope": run.search_scope,
            "memory_scope": run.memory_scope,
            "risk_level": run.risk_level,
            "context": self._build_analysis_context(context),
        }
        result = await agent.process(
            prompt=self._analysis_prompt(analysis_payload),
            preferred_models=self._role_models_for("analyst"),
        )
        normalized = self._normalize_analysis_result(result, context)
        return {
            "output_payload": normalized,
            "output_summary": _compact_text(normalized.get("summary", ""), 1000),
            "evidence": [],
            "conversations": self._conversation_entries(agent, phase="analysis"),
            "tokens": self._aggregate_latest_tokens(agent),
            "duration": self._latest_duration(agent),
            "model": self._latest_model(agent),
            "provider": self._latest_provider(agent),
            "response": self._latest_response(agent),
            "prompt": self._latest_prompt(agent),
            "system_prompt": self._analysis_system_prompt(),
        }

    async def _run_internal_action(
        self,
        *,
        db,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
        allowed_actions: set[str],
    ) -> dict[str, Any]:
        action_name = node.action or str(node.parameters.get("action") or "").strip()
        if not action_name:
            raise ValueError("internal_action nodes require an action name")
        if action_name not in allowed_actions:
            raise ValueError(f"Action '{action_name}' is not in the allow-list")

        action_request = ActionRequest(
            action=action_name,
            parameters=dict(node.parameters or {}),
            rationale=node.instruction or "",
            metadata={"step_key": node.step_key, "task_uuid": task.task_uuid},
        )

        child_task_type = self._map_action_to_task_type(action_name)
        child_task_name = f"Agent action: {action_name}"
        child_task = await task_manager.create_task(
            task_type=child_task_type,
            task_name=child_task_name,
            country_id=run.country_id,
            parent_task_id=task.id,
            priority=TaskPriority.HIGH,
            input_data=dict(action_request.parameters),
            description=action_request.rationale or f"Executed by agent workflow step {node.step_key}",
        )

        await task_manager.update_task_status(child_task.task_uuid, TaskStatus.RUNNING)
        try:
            await task_manager.add_workbook_entry(
                child_task.task_uuid,
                entry_type="info",
                title="Inline Agent Action Started",
                content=(
                    f"Action: {action_name}\n"
                    f"Parent task: {task.task_uuid}\n"
                    f"Step: {node.step_key}"
                ),
                content_type="text",
                metadata={"parent_task_uuid": task.task_uuid, "action": action_name, "step_key": node.step_key},
            )
        except Exception as log_exc:
            logger.warning("Failed to add inline action start log for %s: %s", child_task.task_uuid, log_exc)

        try:
            if action_name == "crawl_data":
                result = await self._execute_crawl(child_task, action_request.parameters)
            elif action_name == "generate_report":
                result = await self._execute_report(child_task, action_request.parameters)
            elif action_name == "update_disease_knowledge":
                result = await self._execute_knowledge(child_task, action_request.parameters)
            elif action_name == "export_data":
                result = await self._execute_export(child_task, action_request.parameters)
            else:
                raise ValueError(f"Unsupported action: {action_name}")
        except Exception as exc:
            await task_manager.update_task_status(child_task.task_uuid, TaskStatus.FAILED, error_message=str(exc))
            try:
                await task_manager.add_workbook_entry(
                    child_task.task_uuid,
                    entry_type="error",
                    title="Inline Agent Action Failed",
                    content=str(exc),
                    content_type="text",
                    metadata={"parent_task_uuid": task.task_uuid, "action": action_name, "step_key": node.step_key},
                )
            except Exception as log_exc:
                logger.warning("Failed to add inline action failure log for %s: %s", child_task.task_uuid, log_exc)
            raise

        action_result = ActionResult(
            action=action_name,
            success=True,
            summary=_compact_text(json.dumps(result, ensure_ascii=False), 800),
            output=result,
            artifacts=self._extract_artifacts(result),
            evidence=[
                EvidenceRef(
                    evidence_type="action",
                    source_type=action_name,
                    source_name=child_task.task_name,
                    title=child_task.task_name,
                    content_snippet=_compact_text(json.dumps(result, ensure_ascii=False), 800),
                    content_hash=_stable_hash(json.dumps(result, sort_keys=True, ensure_ascii=False)),
                    confidence=0.9,
                    metadata={"child_task_uuid": child_task.task_uuid, "action": action_name},
                )
            ],
            metadata={"child_task_uuid": child_task.task_uuid, "action": action_name},
        )

        async with get_database() as child_db:
            child_db_task = await child_db.get(Task, child_task.id)
            if child_db_task is not None:
                child_db_task.output_data = result
                child_db_task.status = TaskStatus.COMPLETED
                child_db_task.completed_at = _now()
                await child_db.commit()

        await task_manager.update_task_status(child_task.task_uuid, TaskStatus.COMPLETED)
        try:
            await task_manager.add_workbook_entry(
                child_task.task_uuid,
                entry_type="success",
                title="Inline Agent Action Completed",
                content=_compact_text(json.dumps(result, ensure_ascii=False), 1200),
                content_type="json",
                metadata={"parent_task_uuid": task.task_uuid, "action": action_name, "step_key": node.step_key},
            )
        except Exception as log_exc:
            logger.warning("Failed to add inline action completion log for %s: %s", child_task.task_uuid, log_exc)

        return {
            "output_payload": action_result.to_dict(),
            "output_summary": action_result.summary,
            "evidence": action_result.evidence,
            "conversations": [],
            "tokens": {},
            "duration": 0.0,
            "model": None,
            "provider": None,
            "response": None,
            "prompt": None,
            "system_prompt": None,
        }

    async def _run_review(
        self,
        *,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        reviewer = WorkflowAgent(
            name="Reviewer",
            system_prompt=self._review_system_prompt(),
            temperature=0.1,
            max_tokens=min(self.step_token_budget, 2500),
        )
        reviewer.clear_conversation_history()
        review_payload = {
            "prompt": prompt,
            "analysis": context.get("analysis") or {},
            "evidence": context.get("evidence", [])[:20],
            "actions": context.get("actions", []),
            "open_questions": context.get("open_questions", []),
            "findings": context.get("findings", []),
        }
        result = await reviewer.process(
            prompt=self._review_prompt(review_payload),
            preferred_models=self._role_models_for("reviewer"),
        )
        normalized = self._normalize_review_result(result)
        run.review_round_count += 1
        return {
            "output_payload": normalized,
            "output_summary": _compact_text(normalized.get("assessment", ""), 1000),
            "evidence": [],
            "conversations": self._conversation_entries(reviewer, phase="review"),
            "tokens": self._aggregate_latest_tokens(reviewer),
            "duration": self._latest_duration(reviewer),
            "model": self._latest_model(reviewer),
            "provider": self._latest_provider(reviewer),
            "response": self._latest_response(reviewer),
            "prompt": self._latest_prompt(reviewer),
            "system_prompt": self._review_system_prompt(),
        }

    async def _run_finalize(
        self,
        *,
        db,
        task: Task,
        run: AgentWorkflowRun,
        node: PlanNode,
        context: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        synthesizer = WorkflowAgent(
            name="Synthesizer",
            system_prompt=self._synthesizer_system_prompt(),
            temperature=0.2,
            max_tokens=min(self.step_token_budget, 3000),
        )
        synthesizer.clear_conversation_history()
        final_payload = {
            "prompt": prompt,
            "analysis": context.get("analysis") or {},
            "review": context.get("review") or {},
            "actions": context.get("actions", []),
            "evidence": context.get("evidence", [])[:30],
            "open_questions": context.get("open_questions", []),
            "plan": run.plan_json or context.get("plan", []),
        }
        result = await synthesizer.process(
            prompt=self._synthesizer_prompt(final_payload),
            preferred_models=self._role_models_for("synthesizer"),
        )
        normalized = self._normalize_final_result(result, context, run)
        await self._store_final_memory(db, run, normalized)
        return {
            "output_payload": normalized.to_dict(),
            "output_summary": normalized.summary,
            "evidence": [],
            "conversations": self._conversation_entries(synthesizer, phase="finalize"),
            "tokens": self._aggregate_latest_tokens(synthesizer),
            "duration": self._latest_duration(synthesizer),
            "model": self._latest_model(synthesizer),
            "provider": self._latest_provider(synthesizer),
            "response": self._latest_response(synthesizer),
            "prompt": self._latest_prompt(synthesizer),
            "system_prompt": self._synthesizer_system_prompt(),
        }

    def _maybe_schedule_replan_nodes(
        self,
        *,
        run: AgentWorkflowRun,
        node: PlanNode,
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> list[PlanNode]:
        if run.replan_count >= self.max_replan_rounds:
            return []
        if result.get("approved", False):
            return []
        follow_up_queries = self._unique_items(
            _ensure_list(result.get("follow_up_search_queries"))
            + _ensure_list(result.get("rewrite_instruction"))
        )
        if not follow_up_queries:
            follow_up_queries = self._extract_follow_up_queries(result, context)
        if not follow_up_queries:
            return []

        run.replan_count += 1
        base = f"replan{run.replan_count}"
        nodes = [
            PlanNode(
                step_key=f"{base}_web_search",
                step_type="web_search",
                title="Replan web search",
                instruction="Refresh web evidence based on reviewer feedback.",
                search_queries=follow_up_queries,
                max_results=4,
                metadata={"replan": run.replan_count},
            ),
            PlanNode(
                step_key=f"{base}_db_lookup",
                step_type="db_lookup",
                title="Replan database lookup",
                instruction="Refresh database evidence based on reviewer feedback.",
                search_queries=follow_up_queries,
                target_tables=list(self._db_search_registry().keys()),
                max_results=4,
                metadata={"replan": run.replan_count},
            ),
            PlanNode(
                step_key=f"{base}_analysis",
                step_type="analysis",
                title="Replan analysis",
                instruction="Integrate follow-up evidence and close the gaps identified by review.",
                metadata={"replan": run.replan_count},
            ),
            PlanNode(
                step_key=f"{base}_review",
                step_type="review",
                title="Replan review",
                instruction="Check whether the follow-up pass resolved the reviewer concerns.",
                metadata={"replan": run.replan_count},
            ),
            PlanNode(
                step_key=f"{base}_finalize",
                step_type="finalize",
                title="Replan finalize",
                instruction="Produce the final evidence report from the revised evidence set.",
                metadata={"replan": run.replan_count},
            ),
        ]
        return nodes

    async def _build_final_output(
        self,
        db,
        run: AgentWorkflowRun,
        task: Task,
        prompt: str,
        context: dict[str, Any],
        plan_nodes: list[PlanNode],
    ) -> AgentFinalResult:
        analysis = context.get("analysis") or {}
        review = context.get("review") or {}
        actions = context.get("actions", [])
        evidence = context.get("evidence", [])
        findings = self._normalize_findings(analysis, evidence)
        citations = self._normalize_citations(evidence, findings)
        open_questions = self._unique_items(
            _ensure_list(analysis.get("open_questions"))
            + _ensure_list(review.get("missing_evidence"))
            + _ensure_list(review.get("issues"))
        )
        artifacts = self._normalize_artifacts(analysis, actions, evidence)
        summary = _compact_text(
            analysis.get("summary")
            or review.get("assessment")
            or prompt,
            1200,
        )
        run_log_digest = self._build_run_log_digest(run, plan_nodes, analysis, review, actions)
        return AgentFinalResult(
            summary=summary,
            findings=findings,
            citations=citations,
            actions_taken=actions,
            artifacts=artifacts,
            open_questions=open_questions,
            run_log_digest=run_log_digest,
            risk_level=run.risk_level,
            status="completed",
            confidence=self._infer_confidence(findings, citations, review),
            evidence_count=len(evidence),
            step_count=len(plan_nodes),
            metadata={
                "review": review,
                "analysis": analysis,
                "task_uuid": task.task_uuid,
            },
        )

    async def _store_workflow_memory(
        self,
        db,
        run: AgentWorkflowRun,
        task: Task,
        prompt: str,
        final_output: AgentFinalResult,
    ) -> None:
        memory = AgentWorkflowMemory(
            run_id=run.id,
            task_id=task.id,
            scope=run.memory_scope,
            memory_type="workflow_summary",
            content=final_output.summary,
            summary=_compact_text(final_output.summary, 500),
            source_type="workflow",
            source_ref=task.task_uuid,
            content_hash=_stable_hash(json.dumps(final_output.to_dict(), sort_keys=True, ensure_ascii=False)),
            embedding=self._embed_text(final_output.summary),
            collection_name=self.config.qdrant.collection_name,
            status="active",
            metadata_={
                "task_uuid": task.task_uuid,
                "prompt_hash": _stable_hash(prompt),
                "findings": len(final_output.findings),
            },
        )
        db.add(memory)
        await db.flush()
        await self._upsert_qdrant_memory(memory)

    async def _store_final_memory(self, db, run: AgentWorkflowRun, final_output: AgentFinalResult) -> None:
        if run.task_id is None:
            return
        task = await db.get(Task, run.task_id)
        if task is None:
            return
        await self._store_workflow_memory(db, run, task, run.prompt, final_output)

    async def _upsert_evidence(self, db, run: AgentWorkflowRun, step: AgentWorkflowStep, evidence: EvidenceRef) -> None:
        if not evidence.content_hash:
            evidence.content_hash = _stable_hash(
                f"{evidence.source_type}:{evidence.url or evidence.title}:{evidence.content_snippet}"
            )
        existing = (
            await db.execute(
                select(AgentWorkflowEvidence).where(
                    AgentWorkflowEvidence.run_id == run.id,
                    AgentWorkflowEvidence.content_hash == evidence.content_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        db.add(
            AgentWorkflowEvidence(
                run_id=run.id,
                step_id=step.id,
                evidence_type=evidence.evidence_type,
                source_type=evidence.source_type,
                source_name=evidence.source_name,
                title=evidence.title,
                url=evidence.url,
                resolved_url=evidence.resolved_url,
                content_snippet=evidence.content_snippet,
                content_hash=evidence.content_hash,
                confidence=evidence.confidence,
                weight=evidence.weight,
                metadata_=evidence.metadata,
            )
        )
        await db.flush()

    def _build_initial_context(
        self,
        *,
        prompt: str,
        payload: dict[str, Any],
        search_scope: str,
        memory_scope: str,
    ) -> dict[str, Any]:
        return {
            "prompt": prompt,
            "payload": payload,
            "search_scope": search_scope,
            "memory_scope": memory_scope,
            "evidence": [],
            "analysis": {},
            "review": {},
            "actions": [],
            "open_questions": [],
            "findings": [],
            "plan": [],
        }

    def _merge_step_context(self, context: dict[str, Any], step_output: dict[str, Any], step_type: str) -> dict[str, Any]:
        merged = dict(context)
        merged[step_type] = step_output
        if step_type in {"web_search", "db_lookup", "memory_lookup", "internal_action"}:
            merged.setdefault("evidence", [])
            merged["evidence"].extend(_ensure_list(step_output.get("results") or step_output.get("evidence")))
            if step_type == "internal_action":
                merged.setdefault("actions", [])
                merged["actions"].append(step_output)
        if step_type == "analysis":
            merged["analysis"] = step_output
            merged["findings"] = step_output.get("findings", [])
            merged["open_questions"] = step_output.get("open_questions", [])
        if step_type == "review":
            merged["review"] = step_output
            merged.setdefault("open_questions", [])
            merged["open_questions"].extend(_ensure_list(step_output.get("missing_evidence")))
            merged["open_questions"].extend(_ensure_list(step_output.get("issues")))
        if step_type == "finalize":
            merged["final"] = step_output
        return merged

    def _normalize_actions(self, value: Any) -> set[str]:
        actions = set()
        for item in _ensure_list(value):
            if isinstance(item, str) and item.strip():
                actions.add(item.strip())
        if not actions:
            return set(DEFAULT_ALLOWED_ACTIONS)
        return actions

    def _normalize_analysis_result(self, result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        summary = _compact_text(result.get("summary") or result.get("assessment") or "", 1200)
        findings = self._normalize_findings(result, context.get("evidence", []))
        open_questions = self._unique_items(_ensure_list(result.get("open_questions")))
        return {
            "summary": summary,
            "findings": findings,
            "open_questions": open_questions,
            "evidence_map": result.get("evidence_map") or {},
            "confidence": self._coerce_float(result.get("confidence"), default=0.6),
            "notes": result.get("notes") or result.get("raw_response"),
        }

    def _normalize_review_result(self, result: dict[str, Any]) -> dict[str, Any]:
        approved = bool(result.get("approved", False))
        issues = self._unique_items(_ensure_list(result.get("issues")))
        missing = self._unique_items(_ensure_list(result.get("missing_evidence")) + _ensure_list(result.get("follow_up_search_queries")))
        return {
            "approved": approved,
            "score": self._coerce_float(result.get("score"), default=0.6),
            "issues": issues,
            "missing_evidence": missing,
            "rewrite_instruction": _compact_text(result.get("rewrite_instruction") or "", 1200),
            "follow_up_search_queries": self._unique_items(_ensure_list(result.get("follow_up_search_queries"))),
            "assessment": _compact_text(result.get("assessment") or result.get("raw_response") or "", 1200),
            "confidence": self._coerce_float(result.get("confidence"), default=0.6),
        }

    def _normalize_final_result(
        self,
        result: dict[str, Any],
        context: dict[str, Any],
        run: AgentWorkflowRun,
    ) -> AgentFinalResult:
        summary = _compact_text(result.get("summary") or context.get("analysis", {}).get("summary") or "", 1200)
        findings = self._normalize_findings(result, context.get("evidence", []))
        citations = self._normalize_citations(context.get("evidence", []), findings)
        actions = self._normalize_actions_taken(context)
        artifacts = self._normalize_artifacts(result, actions, context.get("evidence", []))
        open_questions = self._unique_items(
            _ensure_list(result.get("open_questions"))
            + _ensure_list(context.get("open_questions"))
        )
        run_log_digest = _compact_text(result.get("run_log_digest") or "", 1200)
        if not run_log_digest:
            run_log_digest = self._build_run_log_digest(run, [], result, context.get("review", {}), actions)
        return AgentFinalResult(
            summary=summary,
            findings=findings,
            citations=citations,
            actions_taken=actions,
            artifacts=artifacts,
            open_questions=open_questions,
            run_log_digest=run_log_digest,
            risk_level=str(result.get("risk_level") or run.risk_level or "medium"),
            status=str(result.get("status") or "completed"),
            confidence=self._coerce_float(result.get("confidence"), default=0.7),
            evidence_count=len(context.get("evidence", [])),
            step_count=len(context.get("plan", [])) or run.step_count,
            metadata={"raw": result},
        )

    def _normalize_findings(self, payload: dict[str, Any], evidence: list[Any]) -> list[dict[str, Any]]:
        evidence_hashes: list[str] = []
        for item in evidence:
            if isinstance(item, EvidenceRef):
                if item.content_hash:
                    evidence_hashes.append(item.content_hash)
            elif isinstance(item, dict):
                content_hash = item.get("content_hash")
                if isinstance(content_hash, str) and content_hash:
                    evidence_hashes.append(content_hash)
        findings = []
        for item in _ensure_list(payload.get("findings")):
            if not isinstance(item, dict):
                item = {"claim": str(item)}
            item = dict(item)
            item["supporting_evidence"] = self._unique_items(_ensure_list(item.get("supporting_evidence")) or _ensure_list(item.get("evidence_ids")))
            item["claim"] = _compact_text(item.get("claim") or item.get("finding") or "", 500)
            item["confidence"] = self._coerce_float(item.get("confidence"), default=0.5)
            if not item["supporting_evidence"] and evidence_hashes:
                item["supporting_evidence"] = [evidence_hashes[len(findings) % len(evidence_hashes)]]
            findings.append(item)
        if findings:
            return findings
        for idx, ev in enumerate(evidence[:5], 1):
            findings.append(
                {
                    "id": idx,
                    "claim": _compact_text(getattr(ev, "title", None) or ev.get("title") or ev.get("content_snippet") or "", 500),
                    "supporting_evidence": [getattr(ev, "content_hash", None) or ev.get("content_hash")],
                    "confidence": self._coerce_float(getattr(ev, "confidence", None) or ev.get("confidence"), default=0.5),
                }
            )
        return findings

    def _normalize_citations(self, evidence: list[Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        citations = []
        for ev in evidence[:20]:
            if isinstance(ev, EvidenceRef):
                citations.append(ev.to_dict())
            elif isinstance(ev, dict):
                citations.append(ev)
        if citations:
            return citations
        for finding in findings:
            supporting = _ensure_list(finding.get("supporting_evidence"))
            for ref in supporting:
                citations.append({"evidence_ref": ref, "claim": finding.get("claim")})
        return citations

    def _normalize_artifacts(self, payload: dict[str, Any], actions: list[dict[str, Any]], evidence: list[Any]) -> list[dict[str, Any]]:
        artifacts = []
        for item in _ensure_list(payload.get("artifacts")):
            if isinstance(item, dict):
                artifacts.append(item)
            else:
                artifacts.append({"name": str(item)})
        for action in actions:
            if isinstance(action, dict) and action.get("artifacts"):
                artifacts.extend(_ensure_list(action.get("artifacts")))
        return artifacts[:20]

    def _normalize_actions_taken(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        actions = []
        for item in _ensure_list(context.get("actions")):
            if isinstance(item, dict):
                actions.append(item)
        return actions

    def _build_run_log_digest(
        self,
        run: AgentWorkflowRun,
        plan_nodes: list[PlanNode],
        analysis: dict[str, Any],
        review: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> str:
        parts = [
            f"risk={run.risk_level}",
            f"steps={len(plan_nodes) or run.step_count}",
            f"actions={len(actions)}",
            f"analysis={_compact_text(analysis.get('summary') or '', 180)}",
            f"review={_compact_text(review.get('assessment') or '', 180)}",
        ]
        return " | ".join(parts)

    def _infer_confidence(self, findings: list[dict[str, Any]], citations: list[dict[str, Any]], review: dict[str, Any]) -> float:
        base = 0.5 + min(0.3, len(citations) / 20.0)
        if review.get("approved", False):
            base += 0.1
        return round(min(0.95, base), 3)

    def _build_search_queries(self, queries: list[str], *, prompt: str, max_rounds: int) -> list[str]:
        base = self._unique_items([_compact_text(q, 120) for q in queries if q])
        if not base:
            base = [" ".join(_extract_keywords(prompt, 6)) or prompt[:120]]
        expanded = list(base)
        keywords = _extract_keywords(prompt, 6)
        if keywords:
            expanded.append(" ".join(keywords))
            expanded.append(f"{' '.join(keywords)} site:who.int")
            expanded.append(f"{' '.join(keywords)} site:cdc.gov")
            expanded.append(f"{' '.join(keywords)} site:nih.gov")
        if max_rounds <= 1:
            return self._unique_items(expanded)[:2]
        return self._unique_items(expanded)[: max(2, max_rounds * 2)]

    def _duckduckgo_search(self, query: str, max_results: int) -> list[EvidenceRef]:
        params = {"q": query}
        results: list[EvidenceRef] = []
        try:
            response = self.web_session.get(WEB_SEARCH_ENDPOINT, params=params, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Web search failed for %s: %s", query, exc)
            return results

        soup = BeautifulSoup(response.text, "html.parser")
        anchors = soup.select("a.result__a")[: max_results * 2]
        for anchor in anchors:
            title = _compact_text(anchor.get_text(" ", strip=True), 200)
            url = anchor.get("href") or ""
            if not url:
                continue
            snippet = ""
            parent = anchor.find_parent("div", class_="result")
            if parent:
                snippet_node = parent.select_one(".result__snippet")
                if snippet_node:
                    snippet = _compact_text(snippet_node.get_text(" ", strip=True), 500)
            resolved = url
            page_title = title
            page_snippet = snippet
            if url and len(results) < max_results:
                fetched_title, fetched_snippet, resolved = self._fetch_web_page(url)
                if fetched_title:
                    page_title = fetched_title
                if fetched_snippet:
                    page_snippet = fetched_snippet
            content = page_snippet or title
            results.append(
                EvidenceRef(
                    evidence_type="web",
                    source_type=self._guess_source_type(url),
                    source_name=self._guess_source_name(url),
                    title=page_title or title or url,
                    url=url,
                    resolved_url=resolved,
                    content_snippet=content,
                    content_hash=_stable_hash(f"{url}|{content}"),
                    confidence=0.7 if page_snippet else 0.5,
                    metadata={"query": query},
                )
            )
            if len(results) >= max_results:
                break
        return self._unique_evidence(results)

    def _fetch_web_page(self, url: str) -> tuple[str, str, str]:
        try:
            response = self.web_session.get(url, timeout=20, allow_redirects=True)
            response.raise_for_status()
        except Exception:
            return "", "", url
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            title = ""
            if soup.title and soup.title.string:
                title = _compact_text(soup.title.string, 220)
            text_blocks: list[str] = []
            for tag in soup.select("article, main, p, li, h1, h2, h3"):
                text = _compact_text(tag.get_text(" ", strip=True), 220)
                if text:
                    text_blocks.append(text)
                if len(" ".join(text_blocks)) > 1200:
                    break
            snippet = _compact_text(" ".join(text_blocks), 900)
            return title, snippet, response.url or url
        except Exception:
            return "", "", response.url or url

    def _guess_source_type(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if "who.int" in host:
            return "who"
        if "cdc.gov" in host:
            return "cdc"
        if "nih.gov" in host or "ncbi.nlm.nih.gov" in host:
            return "nih"
        if "wikipedia.org" in host:
            return "wikipedia"
        return "web"

    def _guess_source_name(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if not host:
            return "web"
        return host

    def _db_search_registry(self) -> dict[str, tuple[Any, list[Any]]]:
        return {
            "countries": (Country, [Country.code, Country.name, Country.name_en, Country.name_local]),
            "diseases": (Disease, [Disease.name, Disease.name_en, Disease.category, Disease.description]),
            "standard_diseases": (
                StandardDisease,
                [
                    StandardDisease.disease_id,
                    StandardDisease.standard_name_en,
                    StandardDisease.standard_name_zh,
                    StandardDisease.category,
                    StandardDisease.description,
                ],
            ),
            "disease_mappings": (
                DiseaseMapping,
                [
                    DiseaseMapping.disease_id,
                    DiseaseMapping.country_code,
                    DiseaseMapping.local_name,
                    DiseaseMapping.category,
                    DiseaseMapping.source,
                ],
            ),
            "disease_records": (
                DiseaseRecord,
                [DiseaseRecord.data_source, DiseaseRecord.region, DiseaseRecord.city],
            ),
            "reports": (Report, [Report.title, Report.summary, Report.error_message]),
            "report_sections": (ReportSection, [ReportSection.title, ReportSection.content, ReportSection.verification_notes]),
            "crawl_runs": (CrawlRun, [CrawlRun.country_code, CrawlRun.source, CrawlRun.status, CrawlRun.error_message]),
        }

    async def _search_database_table(self, db, table_name: str, terms: list[str], limit: int = 5) -> list[EvidenceRef]:
        registry = self._db_search_registry()
        if table_name not in registry:
            return []
        model, columns = registry[table_name]
        results: list[EvidenceRef] = []
        unique_terms = [term for term in self._unique_items([_compact_text(t, 80) for t in terms]) if term]
        if not unique_terms:
            unique_terms = [" ".join(_extract_keywords(" ".join(terms), 5))]

        if model is DiseaseRecord:
            query = (
                select(DiseaseRecord, Disease.name.label("disease_name"), Country.name_en.label("country_name"))
                .join(Disease, Disease.id == DiseaseRecord.disease_id)
                .join(Country, Country.id == DiseaseRecord.country_id)
            )
            conditions = []
            for term in unique_terms:
                like = f"%{term}%"
                conditions.append(
                    or_(
                        DiseaseRecord.data_source.ilike(like),
                        DiseaseRecord.region.ilike(like),
                        DiseaseRecord.city.ilike(like),
                        Disease.name.ilike(like),
                        Disease.name_en.ilike(like),
                        Country.name.ilike(like),
                        Country.name_en.ilike(like),
                    )
                )
            if conditions:
                query = query.where(or_(*conditions))
            rows = (await db.execute(query.limit(limit))).all()
            for row in rows:
                record = row.DiseaseRecord
                snippet = (
                    f"{row.disease_name} | {row.country_name} | "
                    f"{record.time.date()} | cases={record.cases} deaths={record.deaths} "
                    f"source={record.data_source or ''} region={record.region or ''} city={record.city or ''}"
                )
                results.append(
                    EvidenceRef(
                        evidence_type="db",
                        source_type="disease_records",
                        source_name="disease_records",
                        title=f"{row.disease_name} / {row.country_name}",
                        content_snippet=_compact_text(snippet, 900),
                        content_hash=_stable_hash(snippet),
                        confidence=0.8,
                        metadata={"table": table_name, "time": record.time.isoformat()},
                    )
                )
            return self._unique_evidence(results)

        query = select(model)
        conditions = []
        for term in unique_terms:
            like = f"%{term}%"
            per_term = [col.ilike(like) for col in columns if hasattr(col, "ilike")]
            if per_term:
                conditions.append(or_(*per_term))
        if conditions:
            query = query.where(or_(*conditions))
        rows = (await db.execute(query.limit(limit))).scalars().all()
        for row in rows:
            snippet = self._row_to_snippet(row)
            results.append(
                EvidenceRef(
                    evidence_type="db",
                    source_type=table_name,
                    source_name=table_name,
                    title=self._row_title(row, table_name),
                    content_snippet=_compact_text(snippet, 900),
                    content_hash=_stable_hash(f"{table_name}:{snippet}"),
                    confidence=0.75,
                    metadata={"table": table_name, "row_id": getattr(row, "id", None)},
                )
            )
        return self._unique_evidence(results)

    def _row_title(self, row: Any, table_name: str) -> str:
        for attr in ("title", "name", "name_en", "standard_name_en", "disease_id", "local_name", "code", "country_code"):
            value = getattr(row, attr, None)
            if value:
                return _compact_text(value, 120)
        return table_name

    def _row_to_snippet(self, row: Any) -> str:
        if hasattr(row, "to_dict"):
            payload = row.to_dict()
        else:
            payload = {column.name: getattr(row, column.name, None) for column in row.__table__.columns} if hasattr(row, "__table__") else {}
        pieces = []
        for key in sorted(payload.keys()):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            pieces.append(f"{key}={value}")
            if len(" | ".join(pieces)) > 1000:
                break
        return " | ".join(pieces)

    async def _search_memory(self, db, run: AgentWorkflowRun, *, prompt: str, terms: list[str], limit: int = 5) -> list[EvidenceRef]:
        unique_terms = [term for term in self._unique_items([_compact_text(t, 80) for t in terms]) if term]
        if not unique_terms:
            unique_terms = _extract_keywords(prompt, 5)

        if self._qdrant_enabled():
            qdrant_results = await asyncio.to_thread(self._search_qdrant_memory, prompt, limit)
            if qdrant_results:
                return qdrant_results

        query = select(AgentWorkflowMemory).where(AgentWorkflowMemory.status == "active")
        conditions = []
        for term in unique_terms:
            like = f"%{term}%"
            conditions.append(
                or_(
                    AgentWorkflowMemory.summary.ilike(like),
                    AgentWorkflowMemory.content.ilike(like),
                    AgentWorkflowMemory.source_ref.ilike(like),
                )
            )
        if conditions:
            query = query.where(or_(*conditions))
        rows = (await db.execute(query.order_by(AgentWorkflowMemory.created_at.desc()).limit(limit))).scalars().all()
        results: list[EvidenceRef] = []
        for row in rows:
            snippet = row.summary or row.content or row.source_ref or ""
            results.append(
                EvidenceRef(
                    evidence_type="memory",
                    source_type=row.source_type or "memory",
                    source_name=row.memory_type,
                    title=row.summary or row.memory_type,
                    content_snippet=_compact_text(snippet, 800),
                    content_hash=row.content_hash,
                    confidence=0.7,
                    metadata={
                        "memory_uuid": row.memory_uuid,
                        "scope": row.scope,
                        "collection_name": row.collection_name,
                    },
                )
            )
        return self._unique_evidence(results)

    def _qdrant_enabled(self) -> bool:
        try:
            return bool(self.config.qdrant.url)
        except Exception:
            return False

    def _qdrant_client(self):
        if self._memory_client_checked:
            return self._memory_client
        self._memory_client_checked = True
        try:
            from qdrant_client import QdrantClient

            self._memory_client = QdrantClient(url=self.config.qdrant.url, api_key=self.config.qdrant.api_key)
        except Exception as exc:
            logger.info("Qdrant client unavailable, falling back to Postgres memory: %s", exc)
            self._memory_client = None
        return self._memory_client

    def _search_qdrant_memory(self, prompt: str, limit: int) -> list[EvidenceRef]:
        client = self._qdrant_client()
        if client is None:
            return []
        try:
            from qdrant_client.http import models as qm

            self._ensure_qdrant_collection(client)
            vector = self._embed_text(prompt)
            hits = client.search(
                collection_name=self.config.qdrant.collection_name,
                query_vector=vector,
                limit=limit,
                with_payload=True,
            )
        except Exception as exc:
            logger.debug("Qdrant memory search failed: %s", exc)
            return []
        results: list[EvidenceRef] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                EvidenceRef(
                    evidence_type="memory",
                    source_type=str(payload.get("source_type") or "memory"),
                    source_name=str(payload.get("source_name") or "memory"),
                    title=str(payload.get("summary") or payload.get("content") or "memory"),
                    content_snippet=_compact_text(payload.get("summary") or payload.get("content") or "", 800),
                    content_hash=str(payload.get("content_hash") or ""),
                    confidence=0.7,
                    metadata={"qdrant_score": getattr(hit, "score", None), "qdrant_id": str(hit.id)},
                )
            )
        return self._unique_evidence(results)

    def _ensure_qdrant_collection(self, client) -> None:
        try:
            from qdrant_client.http import models as qm

            collections = client.get_collections().collections
            names = {item.name for item in collections}
            if self.config.qdrant.collection_name in names:
                return
            client.create_collection(
                collection_name=self.config.qdrant.collection_name,
                vectors_config=qm.VectorParams(size=int(self.config.qdrant.vector_size), distance=qm.Distance.COSINE),
            )
        except Exception as exc:
            logger.debug("Qdrant collection ensure failed: %s", exc)

    async def _upsert_qdrant_memory(self, memory: AgentWorkflowMemory) -> None:
        client = self._qdrant_client()
        if client is None:
            return
        try:
            from qdrant_client.http import models as qm

            self._ensure_qdrant_collection(client)
            client.upsert(
                collection_name=self.config.qdrant.collection_name,
                points=[
                    qm.PointStruct(
                        id=memory.memory_uuid,
                        vector=self._embed_text(memory.summary or memory.content or ""),
                        payload={
                            "memory_uuid": memory.memory_uuid,
                            "scope": memory.scope,
                            "memory_type": memory.memory_type,
                            "content": memory.content,
                            "summary": memory.summary,
                            "source_type": memory.source_type,
                            "source_ref": memory.source_ref,
                            "content_hash": memory.content_hash,
                        },
                    )
                ],
            )
        except Exception as exc:
            logger.debug("Qdrant upsert failed: %s", exc)

    def _embed_text(self, text: str) -> list[float]:
        dims = int(self.config.qdrant.vector_size)
        vector = [0.0] * dims
        for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", (text or "").lower()):
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            bucket = int(digest[:8], 16) % dims
            vector[bucket] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        if norm:
            vector = [value / norm for value in vector]
        return vector

    async def _store_memory_from_evidence(self, db, run: AgentWorkflowRun, task: Task, evidence: EvidenceRef, memory_type: str = "evidence") -> None:
        row = AgentWorkflowMemory(
            run_id=run.id,
            task_id=task.id,
            scope=run.memory_scope,
            memory_type=memory_type,
            content=evidence.content_snippet,
            summary=_compact_text(evidence.content_snippet, 500),
            source_type=evidence.source_type,
            source_ref=evidence.url or evidence.resolved_url or evidence.title,
            content_hash=evidence.content_hash or _stable_hash(evidence.content_snippet),
            embedding=self._embed_text(evidence.content_snippet),
            collection_name=self.config.qdrant.collection_name,
            status="active",
            metadata_={"source_name": evidence.source_name, "title": evidence.title},
        )
        existing = (
            await db.execute(
                select(AgentWorkflowMemory).where(
                    AgentWorkflowMemory.content_hash == row.content_hash,
                    AgentWorkflowMemory.scope == row.scope,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        db.add(row)
        await db.flush()
        await self._upsert_qdrant_memory(row)

    async def _store_result_memory(self, db, run: AgentWorkflowRun, task: Task, final_output: AgentFinalResult) -> None:
        memory = AgentWorkflowMemory(
            run_id=run.id,
            task_id=task.id,
            scope=run.memory_scope,
            memory_type="workflow_summary",
            content=final_output.summary,
            summary=_compact_text(final_output.summary, 500),
            source_type="workflow",
            source_ref=task.task_uuid,
            content_hash=_stable_hash(json.dumps(final_output.to_dict(), sort_keys=True, ensure_ascii=False)),
            embedding=self._embed_text(final_output.summary),
            collection_name=self.config.qdrant.collection_name,
            status="active",
            metadata_={"task_uuid": task.task_uuid},
        )
        db.add(memory)
        await db.flush()
        await self._upsert_qdrant_memory(memory)

    async def _persist_agent_conversation(self, db, run: AgentWorkflowRun, step: Optional[AgentWorkflowStep], role: str, phase: str, agent: WorkflowAgent) -> None:
        for entry in agent.get_conversation_history():
            db.add(
                AgentWorkflowConversation(
                    run_id=run.id,
                    step_id=step.id if step else None,
                    agent_role=role,
                    phase=phase,
                    timestamp=self._parse_datetime(entry.get("timestamp")) or _now(),
                    prompt=entry.get("prompt"),
                    system_prompt=entry.get("system_prompt"),
                    response=entry.get("response"),
                    model=entry.get("model"),
                    provider=entry.get("provider"),
                    tokens=entry.get("tokens") or {},
                    duration=entry.get("duration"),
                    temperature=entry.get("temperature"),
                    metadata_=entry.get("metadata") or {},
                )
            )
        await db.flush()

    async def _refresh_run_progress(self, db, run: AgentWorkflowRun, task: Task, completed_steps_count: int) -> None:
        run.step_count = completed_steps_count
        run.budget_tokens_used = self._aggregate_tokens([step.tokens for step in run.steps if str(step.status) == "completed"])
        run.metadata_ = {**(run.metadata_ or {}), "completed_steps": completed_steps_count}
        await db.flush()
        progress = min(99, int((completed_steps_count / max(1, len(run.plan_json or []))) * 100))
        await task_manager.update_task_progress(task.task_uuid, progress)

    def _aggregate_tokens(self, token_payloads: list[Any]) -> int:
        total = 0
        for payload in token_payloads:
            if isinstance(payload, dict):
                try:
                    total += int(payload.get("total") or 0)
                except (TypeError, ValueError):
                    continue
        return total

    def _role_models_for(self, role: str) -> list[str]:
        role = role.lower().strip()
        models = self.role_models.get(role)
        if models:
            return list(models)
        return list(self.config.ai.model_chain)

    def _planner_system_prompt(self) -> str:
        return (
            "You are the planner for a generic multi-expert research workflow. "
            "Return valid JSON only. "
            "Choose the smallest useful plan. "
            "Use step_type values only from: web_search, db_lookup, memory_lookup, analysis, internal_action, review, finalize. "
            "Each node must include step_key, step_type, title, instruction, search_queries, target_tables, action, parameters, depends_on, max_results, confidence, metadata. "
            "Prefer evidence gathering before analysis. "
            "If the prompt implies an internal action and it is allowed, include one internal_action step. "
            "Do not create unnecessary loops. "
            "The JSON object should contain risk_level, summary, and plan."
        )

    def _analysis_system_prompt(self) -> str:
        return (
            "You are the analyst in a generic multi-expert research workflow. "
            "Return valid JSON only. "
            "Derive findings only from the provided evidence and context. "
            "Every finding must reference supporting evidence hashes or URLs when possible. "
            "If evidence is insufficient, state an open question instead of guessing."
        )

    def _review_system_prompt(self) -> str:
        return (
            "You are the reviewer in a generic multi-expert research workflow. "
            "Return valid JSON only. "
            "Check whether the findings are supported by the evidence, whether actions are consistent, and whether there are unsupported claims. "
            "Report issues clearly and provide follow-up search queries when gaps remain."
        )

    def _synthesizer_system_prompt(self) -> str:
        return (
            "You are the synthesizer in a generic multi-expert research workflow. "
            "Return valid JSON only. "
            "Produce a concise evidence report with summary, findings, citations, actions_taken, artifacts, open_questions, run_log_digest, risk_level, status, and confidence. "
            "Do not add unsupported claims."
        )

    def _planner_prompt(
        self,
        *,
        prompt: str,
        task: Task,
        payload: dict[str, Any],
        search_scope: str,
        allowed_actions: set[str],
    ) -> str:
        request = {
            "task_uuid": task.task_uuid,
            "task_name": task.task_name,
            "prompt": prompt,
            "mode": payload.get("mode") or "research",
            "output_format": payload.get("output_format") or "evidence_report",
            "search_scope": search_scope,
            "allowed_actions": sorted(allowed_actions),
            "memory_scope": payload.get("memory_scope") or "project",
            "country_id": payload.get("country_id"),
            "hints": payload.get("hints") or {},
        }
        return (
            "Create a compact research plan for this task. "
            "Keep the number of steps as small as possible. "
            "If the task only needs evidence gathering, use web_search / db_lookup / memory_lookup / analysis / review / finalize. "
            "If an internal action is required, insert exactly one internal_action node and set its action and parameters. "
            "Output JSON with keys: risk_level, summary, plan. "
            "The plan must be an array of nodes, each with: step_key, step_type, title, instruction, search_queries, target_tables, action, parameters, depends_on, max_results, confidence, metadata.\n\n"
            + json.dumps(request, ensure_ascii=False, indent=2)
        )

    def _analysis_prompt(self, payload: dict[str, Any]) -> str:
        return (
            "Analyze the evidence and produce structured findings. "
            "Output JSON with keys: summary, findings, open_questions, confidence, evidence_map, notes.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _review_prompt(self, payload: dict[str, Any]) -> str:
        return (
            "Review the analysis against the evidence. "
            "Output JSON with keys: approved, score, issues, missing_evidence, rewrite_instruction, follow_up_search_queries, assessment, confidence.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _synthesizer_prompt(self, payload: dict[str, Any]) -> str:
        return (
            "Synthesize the final evidence report. "
            "Output JSON with keys: summary, findings, citations, actions_taken, artifacts, open_questions, run_log_digest, risk_level, status, confidence.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def _parse_plan_nodes(self, result: dict[str, Any]) -> list[PlanNode]:
        raw_plan = result.get("plan") or result.get("nodes") or []
        nodes: list[PlanNode] = []
        for index, item in enumerate(_ensure_list(raw_plan), 1):
            if not isinstance(item, dict):
                continue
            nodes.append(self._plan_node_from_dict(item, default_index=index))
        return nodes

    def _plan_node_from_dict(self, payload: dict[str, Any], default_index: int = 1) -> PlanNode:
        return PlanNode(
            step_key=str(payload.get("step_key") or payload.get("key") or f"step_{default_index}"),
            step_type=str(payload.get("step_type") or payload.get("type") or "analysis"),
            title=str(payload.get("title") or payload.get("name") or f"Step {default_index}"),
            instruction=str(payload.get("instruction") or payload.get("description") or ""),
            depends_on=[str(item) for item in _ensure_list(payload.get("depends_on")) if str(item).strip()],
            search_queries=[_compact_text(item, 120) for item in _ensure_list(payload.get("search_queries")) if _compact_text(item, 120)],
            target_tables=[str(item) for item in _ensure_list(payload.get("target_tables")) if str(item).strip()],
            action=str(payload.get("action") or "") or None,
            parameters=dict(payload.get("parameters") or {}),
            max_results=self._coerce_int(payload.get("max_results"), default=5),
            confidence=self._coerce_float(payload.get("confidence"), default=0.6),
            metadata=dict(payload.get("metadata") or {}),
        )

    def _default_plan(self, prompt: str, search_scope: str, allowed_actions: set[str]) -> list[PlanNode]:
        nodes: list[PlanNode] = []
        if "memory" in search_scope:
            nodes.append(
                PlanNode(
                    step_key="memory_lookup",
                    step_type="memory_lookup",
                    title="Memory lookup",
                    instruction="Search stored workflow memories for related evidence.",
                    search_queries=[prompt],
                    max_results=5,
                )
            )
        if "db" in search_scope:
            nodes.append(
                PlanNode(
                    step_key="db_lookup",
                    step_type="db_lookup",
                    title="Database lookup",
                    instruction="Search internal database tables for relevant facts.",
                    search_queries=[prompt],
                    target_tables=list(self._db_search_registry().keys()),
                    max_results=5,
                )
            )
        if "web" in search_scope:
            nodes.append(
                PlanNode(
                    step_key="web_search",
                    step_type="web_search",
                    title="Web search",
                    instruction="Search the public web for authoritative evidence.",
                    search_queries=self._build_search_queries([prompt], prompt=prompt, max_rounds=self.max_search_rounds),
                    max_results=5,
                )
            )
        if self._looks_like_action_task(prompt, allowed_actions):
            action = self._infer_action_name(prompt, allowed_actions)
            if action:
                nodes.append(
                    PlanNode(
                        step_key="internal_action",
                        step_type="internal_action",
                        title=f"Execute {action}",
                        instruction="Run the requested internal action.",
                        action=action,
                        parameters=self._infer_action_parameters(prompt, action),
                    )
                )
        nodes.extend(
            [
                PlanNode(
                    step_key="analysis",
                    step_type="analysis",
                    title="Analysis",
                    instruction="Analyze the collected evidence.",
                ),
                PlanNode(
                    step_key="review",
                    step_type="review",
                    title="Review",
                    instruction="Review the analysis for unsupported claims.",
                ),
                PlanNode(
                    step_key="finalize",
                    step_type="finalize",
                    title="Finalize",
                    instruction="Synthesize the final evidence report.",
                ),
            ]
        )
        return nodes

    def _looks_like_action_task(self, prompt: str, allowed_actions: set[str]) -> bool:
        text = prompt.lower()
        if "report" in text and "generate_report" in allowed_actions:
            return True
        if any(keyword in text for keyword in ["crawl", "fetch", "download"]) and "crawl_data" in allowed_actions:
            return True
        if any(keyword in text for keyword in ["knowledge", "update disease"]) and "update_disease_knowledge" in allowed_actions:
            return True
        if any(keyword in text for keyword in ["export", "download"]) and "export_data" in allowed_actions:
            return True
        return False

    def _infer_action_name(self, prompt: str, allowed_actions: set[str]) -> Optional[str]:
        text = prompt.lower()
        candidates = []
        if "report" in text:
            candidates.append("generate_report")
        if "crawl" in text or "fetch" in text:
            candidates.append("crawl_data")
        if "knowledge" in text:
            candidates.append("update_disease_knowledge")
        if "export" in text or "download" in text:
            candidates.append("export_data")
        for candidate in candidates:
            if candidate in allowed_actions:
                return candidate
        return None

    def _infer_action_parameters(self, prompt: str, action: str) -> dict[str, Any]:
        if action == "generate_report":
            return {"report_type": "monthly", "language": "en", "days": 365, "enable_review": True}
        if action == "crawl_data":
            return {"country_code": "CN", "source": "all", "force": False, "process": True, "save_raw": True, "fill_missing": True}
        if action == "update_disease_knowledge":
            keywords = _extract_keywords(prompt, 3)
            return {"disease_ids": [keyword.upper() for keyword in keywords[:2]] or ["INFLUENZA"], "source": ["who", "wikidata", "wikipedia"], "force": False, "generator": "ai"}
        if action == "export_data":
            return {"country_code": "CN", "formats": ["csv", "json"], "mode": "latest"}
        return {}

    def _map_action_to_task_type(self, action: str) -> TaskType:
        if action == "crawl_data":
            return TaskType.CRAWL_DATA
        if action == "generate_report":
            return TaskType.GENERATE_REPORT
        if action == "update_disease_knowledge":
            return TaskType.UPDATE_DISEASE_KNOWLEDGE
        if action == "export_data":
            return TaskType.EXPORT_DATA
        raise ValueError(f"Unsupported action: {action}")

    async def _execute_crawl(self, task: Task, payload: dict[str, Any]) -> dict[str, Any]:
        service = CrawlService()
        result = await service.execute(
            task=task,
            country_code=str(payload.get("country_code") or "CN").upper(),
            source=str(payload.get("source") or "all"),
            force=bool(payload.get("force", False)),
            process=bool(payload.get("process", True)),
            save_raw=bool(payload.get("save_raw", True)),
            fill_missing=bool(payload.get("fill_missing", True)),
        )
        return {
            "crawl_run_id": result.crawl_run_id,
            "new_reports": result.new_reports,
            "processed_reports": result.processed_reports,
            "total_records": result.total_records,
        }

    async def _execute_report(self, task: Task, payload: dict[str, Any]) -> dict[str, Any]:
        service = ReportService()
        result = await service.execute(
            task=task,
            country_code=str(payload.get("country_code") or "CN").upper(),
            report_type=str(payload.get("report_type") or "monthly"),
            period_start_iso=payload.get("period_start"),
            period_end_iso=payload.get("period_end"),
            language=str(payload.get("language") or "en"),
            days=self._coerce_int(payload.get("days"), default=365),
            enable_review=bool(payload.get("enable_review", True)),
            send_email=bool(payload.get("send_email", False)),
            reuse_from_failed=bool(payload.get("reuse_from_failed", True)),
            reuse_strategy=str(payload.get("reuse_strategy") or "auto"),
            reuse_report_id=self._coerce_int(payload.get("reuse_report_id")),
            report_id_ref=[None],
        )
        return {
            "report_id": result.report_id,
            "report_uuid": result.report_uuid,
            "status": result.status,
            "sections_count": result.sections_count,
            "files": result.output_files,
            "reused": result.reused,
        }

    async def _execute_knowledge(self, task: Task, payload: dict[str, Any]) -> dict[str, Any]:
        service = DiseaseKnowledgeUpdateService()
        return await service.execute_task(task)

    async def _execute_export(self, task: Task, payload: dict[str, Any]) -> dict[str, Any]:
        country_code = str(payload.get("country_code") or "CN").upper()
        mode = str(payload.get("mode") or "latest").lower()
        formats = [str(item) for item in _ensure_list(payload.get("formats")) if str(item).strip()]
        period_start = self._parse_datetime(payload.get("period_start"))
        period_end = self._parse_datetime(payload.get("period_end"))
        if mode == "all":
            files = await self._exporter.export_all(country_code, period_start=period_start, period_end=period_end, formats=formats or None)
        else:
            files = await self._exporter.export_latest(country_code, formats=formats or None)
        return {"country_code": country_code, "mode": mode, "files": files}

    def _conversation_entries(self, agent: WorkflowAgent, phase: str) -> list[dict[str, Any]]:
        entries = []
        for item in agent.get_conversation_history():
            item = dict(item)
            item["agent_role"] = agent.name.lower()
            item["phase"] = phase
            entries.append(item)
        return entries

    def _aggregate_latest_tokens(self, agent: WorkflowAgent) -> dict[str, Any]:
        latest = agent.get_latest_conversation() or {}
        return latest.get("tokens") if isinstance(latest.get("tokens"), dict) else {}

    def _latest_duration(self, agent: WorkflowAgent) -> float:
        latest = agent.get_latest_conversation() or {}
        try:
            return float(latest.get("duration") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _latest_model(self, agent: WorkflowAgent) -> Optional[str]:
        latest = agent.get_latest_conversation() or {}
        model = latest.get("model")
        return str(model) if isinstance(model, str) and model.strip() else None

    def _latest_provider(self, agent: WorkflowAgent) -> Optional[str]:
        latest = agent.get_latest_conversation() or {}
        provider = latest.get("provider")
        return str(provider) if isinstance(provider, str) and provider.strip() else None

    def _latest_response(self, agent: WorkflowAgent) -> Optional[str]:
        latest = agent.get_latest_conversation() or {}
        response = latest.get("response")
        return str(response) if isinstance(response, str) and response.strip() else None

    def _latest_prompt(self, agent: WorkflowAgent) -> Optional[str]:
        latest = agent.get_latest_conversation() or {}
        prompt = latest.get("prompt")
        return str(prompt) if isinstance(prompt, str) and prompt.strip() else None

    def _serialize_task(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "task_uuid": task.task_uuid,
            "task_name": task.task_name,
            "task_type": str(task.task_type),
            "status": str(task.status),
            "priority": str(task.priority),
            "country_id": task.country_id,
            "report_id": task.report_id,
            "progress": task.progress or 0,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "description": task.description,
            "input_data": task.input_data,
            "output_data": task.output_data,
            "metadata": task.metadata_ or {},
        }

    def _serialize_task_workbook_entry(self, entry: TaskWorkbook) -> dict[str, Any]:
        return {
            "id": entry.id,
            "entry_uuid": entry.entry_uuid,
            "entry_type": entry.entry_type,
            "title": entry.title,
            "content": entry.content,
            "content_type": entry.content_type,
            "prompt": entry.prompt,
            "response": entry.response,
            "model_used": entry.model_used,
            "tokens_used": entry.tokens_used,
            "cost": entry.cost,
            "duration": entry.duration,
            "success": entry.success,
            "error_message": entry.error_message,
            "metadata": entry.metadata_ or {},
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }

    def _serialize_run_row(self, row: Any) -> dict[str, Any]:
        return {
            "task": self._serialize_task(row.Task),
            "country_code": row.country_code,
            "country_name": row.country_name,
            "run": self._serialize_run(row.AgentWorkflowRun),
        }

    def _serialize_run(self, run: AgentWorkflowRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "task_id": run.task_id,
            "mode": run.mode,
            "output_format": run.output_format,
            "prompt": run.prompt,
            "status": run.status,
            "risk_level": run.risk_level,
            "country_id": run.country_id,
            "search_scope": run.search_scope,
            "memory_scope": run.memory_scope,
            "allowed_actions": run.allowed_actions or [],
            "plan_json": run.plan_json or [],
            "summary": run.summary,
            "findings": run.findings or [],
            "citations": run.citations or [],
            "artifacts": run.artifacts or [],
            "open_questions": run.open_questions or [],
            "actions_taken": run.actions_taken or [],
            "result_json": run.result_json or {},
            "budget_tokens_total": run.budget_tokens_total,
            "budget_tokens_used": run.budget_tokens_used,
            "replan_count": run.replan_count,
            "search_round_count": run.search_round_count,
            "review_round_count": run.review_round_count,
            "step_count": run.step_count,
            "error_message": run.error_message,
            "metadata": run.metadata_ or {},
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        }

    def _serialize_step(self, step: AgentWorkflowStep) -> dict[str, Any]:
        return {
            "id": step.id,
            "step_uuid": step.step_uuid,
            "run_id": step.run_id,
            "step_key": step.step_key,
            "step_order": step.step_order,
            "step_type": step.step_type,
            "step_name": step.step_name,
            "status": step.status,
            "attempt": step.attempt,
            "input_summary": step.input_summary,
            "output_summary": step.output_summary,
            "input_payload": step.input_payload or {},
            "output_payload": step.output_payload or {},
            "prompt": step.prompt,
            "system_prompt": step.system_prompt,
            "response": step.response,
            "model": step.model,
            "provider": step.provider,
            "tokens": step.tokens or {},
            "duration": step.duration,
            "error_message": step.error_message,
            "metadata": step.metadata_ or {},
            "created_at": step.created_at.isoformat() if step.created_at else None,
            "updated_at": step.updated_at.isoformat() if step.updated_at else None,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "ended_at": step.ended_at.isoformat() if step.ended_at else None,
        }

    def _serialize_evidence(self, evidence: AgentWorkflowEvidence) -> dict[str, Any]:
        return {
            "id": evidence.id,
            "evidence_uuid": evidence.evidence_uuid,
            "run_id": evidence.run_id,
            "step_id": evidence.step_id,
            "evidence_type": evidence.evidence_type,
            "source_type": evidence.source_type,
            "source_name": evidence.source_name,
            "title": evidence.title,
            "url": evidence.url,
            "resolved_url": evidence.resolved_url,
            "content_snippet": evidence.content_snippet,
            "content_hash": evidence.content_hash,
            "confidence": evidence.confidence,
            "weight": evidence.weight,
            "metadata": evidence.metadata_ or {},
            "created_at": evidence.created_at.isoformat() if evidence.created_at else None,
            "updated_at": evidence.updated_at.isoformat() if evidence.updated_at else None,
        }

    def _serialize_conversation(self, conversation: AgentWorkflowConversation) -> dict[str, Any]:
        return {
            "id": conversation.id,
            "conversation_uuid": conversation.conversation_uuid,
            "run_id": conversation.run_id,
            "step_id": conversation.step_id,
            "agent_role": conversation.agent_role,
            "phase": conversation.phase,
            "timestamp": conversation.timestamp.isoformat() if conversation.timestamp else None,
            "prompt": conversation.prompt,
            "system_prompt": conversation.system_prompt,
            "response": conversation.response,
            "model": conversation.model,
            "provider": conversation.provider,
            "tokens": conversation.tokens or {},
            "duration": conversation.duration,
            "temperature": conversation.temperature,
            "metadata": conversation.metadata_ or {},
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
        }

    def _serialize_memory(self, memory: AgentWorkflowMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "memory_uuid": memory.memory_uuid,
            "run_id": memory.run_id,
            "task_id": memory.task_id,
            "scope": memory.scope,
            "memory_type": memory.memory_type,
            "content": memory.content,
            "summary": memory.summary,
            "source_type": memory.source_type,
            "source_ref": memory.source_ref,
            "content_hash": memory.content_hash,
            "embedding": memory.embedding or [],
            "collection_name": memory.collection_name,
            "qdrant_point_id": memory.qdrant_point_id,
            "status": memory.status,
            "metadata": memory.metadata_ or {},
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        }

    async def _load_run_row(self, db, task_uuid: str):
        query = (
            select(AgentWorkflowRun, Task, Country.code.label("country_code"), Country.name_en.label("country_name"))
            .join(Task, Task.id == AgentWorkflowRun.task_id)
            .outerjoin(Country, Country.id == AgentWorkflowRun.country_id)
            .where(Task.task_uuid == task_uuid)
        )
        return (await db.execute(query)).one_or_none()

    async def _load_or_create_run(
        self,
        db,
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
                    existing.started_at = _now()
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
            budget_tokens_total=self.total_token_budget,
            budget_tokens_used=0,
            replan_count=0,
            search_round_count=0,
            review_round_count=0,
            step_count=0,
            metadata_=payload,
            started_at=_now(),
        )
        db.add(run)
        await db.flush()
        return run

    def _coerce_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _coerce_float(self, value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _unique_items(self, values: list[Any]) -> list[Any]:
        seen = set()
        unique = []
        for item in values:
            if item is None:
                continue
            marker = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        return unique

    def _unique_evidence(self, items: list[EvidenceRef]) -> list[EvidenceRef]:
        seen = set()
        unique = []
        for item in items:
            marker = item.content_hash or _stable_hash(f"{item.source_type}:{item.title}:{item.content_snippet}")
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        return unique

    def _summarize_evidence(self, evidence: list[EvidenceRef], *, title: str) -> str:
        if not evidence:
            return f"{title}: no evidence found"
        snippets = [f"{item.source_name}: {_compact_text(item.content_snippet, 180)}" for item in evidence[:5]]
        return f"{title}: " + " | ".join(snippets)

    def _extract_artifacts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        artifacts = []
        for item in _ensure_list(result.get("files")):
            if isinstance(item, dict):
                artifacts.append(item)
            elif isinstance(item, str):
                artifacts.append({"path": item})
        for item in _ensure_list(result.get("artifacts")):
            if isinstance(item, dict):
                artifacts.append(item)
        return artifacts

    def _extract_follow_up_queries(self, result: dict[str, Any], context: dict[str, Any]) -> list[str]:
        queries = []
        for item in _ensure_list(result.get("follow_up_search_queries")):
            if isinstance(item, str) and item.strip():
                queries.append(item.strip())
        for issue in _ensure_list(result.get("issues")):
            if isinstance(issue, str):
                queries.extend(_extract_keywords(issue, 3))
        if not queries and context.get("open_questions"):
            for item in _ensure_list(context.get("open_questions")):
                if isinstance(item, str):
                    queries.extend(_extract_keywords(item, 3))
        return self._unique_items(queries)

    def _build_analysis_context(self, context: dict[str, Any]) -> dict[str, Any]:
        evidence = []
        for item in context.get("evidence", [])[:30]:
            if isinstance(item, EvidenceRef):
                evidence.append(item.to_dict())
            elif isinstance(item, dict):
                evidence.append(item)
        return {
            "prompt": context.get("prompt"),
            "evidence": evidence,
            "analysis": context.get("analysis") or {},
            "actions": context.get("actions") or [],
            "open_questions": context.get("open_questions") or [],
            "search_scope": context.get("search_scope"),
            "memory_scope": context.get("memory_scope"),
        }

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

agent_workflow_service = AgentWorkflowService()
