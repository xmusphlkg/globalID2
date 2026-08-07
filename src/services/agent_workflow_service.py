"""Generic multi-expert workflow service."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import requests
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
    TaskStatus,
    TaskType,
    TaskWorkbook,
)
from src.generation import DataExporter
from src.services.agent_workflow_types import AgentFinalResult, EvidenceRef, PlanNode
from src.services.agent_workflow import prompts as workflow_prompts
from src.services.agent_workflow import actions as workflow_actions
from src.services.agent_workflow import memory as workflow_memory
from src.services.agent_workflow import repository as workflow_repository
from src.services.agent_workflow import runner as workflow_runner
from src.services.agent_workflow import search as workflow_search
from src.services.agent_workflow import serializers as workflow_serializers
from src.services.agent_workflow import helpers as workflow_helpers
from src.services.agent_workflow.helpers import (
    QUERY_STOPWORDS,
    chunked as _chunked,
    compact_text as _compact_text,
    ensure_list as _ensure_list,
    extract_keywords as _extract_keywords,
    safe_json as _safe_json,
    stable_hash as _stable_hash,
)
from src.services.crawl_service import CrawlService
from src.services.data_release_service import data_release_service
from src.services.disease_knowledge_service import DiseaseKnowledgeUpdateService
from src.services.report_service import ReportService

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWED_ACTIONS = {"crawl_data", "generate_report", "update_disease_knowledge", "export_data"}
WEB_SEARCH_ENDPOINT = workflow_search.WEB_SEARCH_ENDPOINT
WEB_USER_AGENT = workflow_search.WEB_USER_AGENT


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        return await workflow_runner.execute_workflow(
            self,
            task,
            database_factory=get_database,
            task_manager=task_manager,
            now=_now,
            compact_text=_compact_text,
            stable_hash=_stable_hash,
            logger=logger,
        )

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
        return await workflow_repository.get_or_create_run(
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
            total_token_budget=self.total_token_budget,
            now=_now,
        )

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
        return await workflow_repository.load_completed_steps(db, run_id)

    async def _start_step(self, db, run: AgentWorkflowRun, node: PlanNode) -> AgentWorkflowStep:
        return await workflow_repository.start_step(db, run, node, now=_now)

    async def _finish_step(self, db, run: AgentWorkflowRun, step: AgentWorkflowStep, result: dict[str, Any]) -> None:
        await workflow_repository.finish_step(
            db,
            run,
            step,
            result,
            now=_now,
            parse_datetime=self._parse_datetime,
            upsert_evidence=self._upsert_evidence,
        )

    async def _fail_step(self, db, run: AgentWorkflowRun, step: AgentWorkflowStep, exc: Exception) -> None:
        await workflow_repository.fail_step(db, run, step, exc, now=_now)

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
        return await workflow_actions.run_internal_action(
            task=task,
            run=run,
            node=node,
            allowed_actions=allowed_actions,
            execute_action=self._execute_action,
            extract_artifacts=self._extract_artifacts,
            now=_now,
            logger=logger,
        )

    async def _execute_action(
        self, action_name: str, task: Task, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if action_name == "crawl_data":
            return await self._execute_crawl(task, payload)
        if action_name == "generate_report":
            return await self._execute_report(task, payload)
        if action_name == "update_disease_knowledge":
            return await self._execute_knowledge(task, payload)
        if action_name == "export_data":
            return await self._execute_export(task, payload)
        raise ValueError(f"Unsupported action: {action_name}")

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
        return workflow_actions.normalize_actions(value, DEFAULT_ALLOWED_ACTIONS)

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
        return workflow_search.build_search_queries(queries, prompt=prompt, max_rounds=max_rounds)

    def _duckduckgo_search(self, query: str, max_results: int) -> list[EvidenceRef]:
        return workflow_search.duckduckgo_search(
            self.web_session,
            query,
            max_results,
            page_fetcher=self._fetch_web_page,
            source_type_resolver=self._guess_source_type,
            source_name_resolver=self._guess_source_name,
            evidence_deduplicator=self._unique_evidence,
        )

    def _fetch_web_page(self, url: str) -> tuple[str, str, str]:
        return workflow_search.fetch_web_page(self.web_session, url)

    def _guess_source_type(self, url: str) -> str:
        return workflow_search.guess_source_type(url)

    def _guess_source_name(self, url: str) -> str:
        return workflow_search.guess_source_name(url)

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
        return workflow_search.row_title(row, table_name)

    def _row_to_snippet(self, row: Any) -> str:
        return workflow_search.row_to_snippet(row)

    async def _search_memory(self, db, run: AgentWorkflowRun, *, prompt: str, terms: list[str], limit: int = 5) -> list[EvidenceRef]:
        return await workflow_memory.search_memory(
            db,
            prompt=prompt,
            terms=terms,
            limit=limit,
            qdrant_is_enabled=self._qdrant_enabled,
            qdrant_search=self._search_qdrant_memory,
            unique_items=self._unique_items,
            unique_evidence=self._unique_evidence,
        )

    def _qdrant_enabled(self) -> bool:
        return workflow_memory.qdrant_enabled(self.config)

    def _qdrant_client(self):
        if self._memory_client_checked:
            return self._memory_client
        self._memory_client_checked = True
        self._memory_client = workflow_memory.create_qdrant_client(self.config, logger)
        return self._memory_client

    def _search_qdrant_memory(self, prompt: str, limit: int) -> list[EvidenceRef]:
        return workflow_memory.search_qdrant_memory(
            self._qdrant_client(),
            self.config,
            prompt,
            limit,
            embed=self._embed_text,
            ensure_collection=self._ensure_qdrant_collection,
            unique_evidence=self._unique_evidence,
            logger=logger,
        )

    def _ensure_qdrant_collection(self, client) -> None:
        workflow_memory.ensure_qdrant_collection(client, self.config, logger)

    async def _upsert_qdrant_memory(self, memory: AgentWorkflowMemory) -> None:
        workflow_memory.upsert_qdrant_memory(
            self._qdrant_client(),
            self.config,
            memory,
            embed=self._embed_text,
            ensure_collection=self._ensure_qdrant_collection,
            logger=logger,
        )

    def _embed_text(self, text: str) -> list[float]:
        return workflow_memory.embed_text(text, int(self.config.qdrant.vector_size))

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
        return workflow_prompts.planner_system_prompt()

    def _analysis_system_prompt(self) -> str:
        return workflow_prompts.analysis_system_prompt()

    def _review_system_prompt(self) -> str:
        return workflow_prompts.review_system_prompt()

    def _synthesizer_system_prompt(self) -> str:
        return workflow_prompts.synthesizer_system_prompt()

    def _planner_prompt(
        self,
        *,
        prompt: str,
        task: Task,
        payload: dict[str, Any],
        search_scope: str,
        allowed_actions: set[str],
    ) -> str:
        return workflow_prompts.planner_prompt(
            prompt=prompt,
            task_uuid=task.task_uuid,
            task_name=task.task_name,
            payload=payload,
            search_scope=search_scope,
            allowed_actions=allowed_actions,
        )

    def _analysis_prompt(self, payload: dict[str, Any]) -> str:
        return workflow_prompts.analysis_prompt(payload)

    def _review_prompt(self, payload: dict[str, Any]) -> str:
        return workflow_prompts.review_prompt(payload)

    def _synthesizer_prompt(self, payload: dict[str, Any]) -> str:
        return workflow_prompts.synthesizer_prompt(payload)

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
        return workflow_actions.looks_like_action_task(prompt, allowed_actions)

    def _infer_action_name(self, prompt: str, allowed_actions: set[str]) -> Optional[str]:
        return workflow_actions.infer_action_name(prompt, allowed_actions)

    def _infer_action_parameters(self, prompt: str, action: str) -> dict[str, Any]:
        return workflow_actions.infer_action_parameters(prompt, action)

    def _map_action_to_task_type(self, action: str) -> TaskType:
        return workflow_actions.map_action_to_task_type(action)

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
            include_current_month=payload.get("include_current_month"),
            revision_window_months=payload.get("revision_window_months"),
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
        return workflow_serializers.serialize_task(task)

    def _serialize_task_workbook_entry(self, entry: TaskWorkbook) -> dict[str, Any]:
        return workflow_serializers.serialize_task_workbook_entry(entry)

    def _serialize_run_row(self, row: Any) -> dict[str, Any]:
        return {
            "task": self._serialize_task(row.Task),
            "country_code": row.country_code,
            "country_name": row.country_name,
            "run": self._serialize_run(row.AgentWorkflowRun),
        }

    def _serialize_run(self, run: AgentWorkflowRun) -> dict[str, Any]:
        return workflow_serializers.serialize_run(run)

    def _serialize_step(self, step: AgentWorkflowStep) -> dict[str, Any]:
        return workflow_serializers.serialize_step(step)

    def _serialize_evidence(self, evidence: AgentWorkflowEvidence) -> dict[str, Any]:
        return workflow_serializers.serialize_evidence(evidence)

    def _serialize_conversation(self, conversation: AgentWorkflowConversation) -> dict[str, Any]:
        return workflow_serializers.serialize_conversation(conversation)

    def _serialize_memory(self, memory: AgentWorkflowMemory) -> dict[str, Any]:
        return workflow_serializers.serialize_memory(memory)

    async def _load_run_row(self, db, task_uuid: str):
        return await workflow_repository.load_run_row(db, task_uuid)

    def _coerce_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        return workflow_helpers.coerce_int(value, default)

    def _coerce_float(self, value: Any, default: float = 0.0) -> float:
        return workflow_helpers.coerce_float(value, default)

    def _unique_items(self, values: list[Any]) -> list[Any]:
        return workflow_helpers.unique_items(values)

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
        return workflow_helpers.parse_datetime(value)

agent_workflow_service = AgentWorkflowService()
