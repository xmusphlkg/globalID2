"""Stable API serializers for agent workflow domain rows."""
from __future__ import annotations

from typing import Any


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def serialize_task(task: Any) -> dict[str, Any]:
    return {
        "id": task.id, "task_uuid": task.task_uuid, "task_name": task.task_name,
        "task_type": str(task.task_type), "status": str(task.status), "priority": str(task.priority),
        "country_id": task.country_id, "report_id": task.report_id, "progress": task.progress or 0,
        "created_at": _iso(task.created_at), "started_at": _iso(task.started_at),
        "completed_at": _iso(task.completed_at), "description": task.description,
        "input_data": task.input_data, "output_data": task.output_data, "metadata": task.metadata_ or {},
    }


def serialize_task_workbook_entry(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id, "entry_uuid": entry.entry_uuid, "entry_type": entry.entry_type,
        "title": entry.title, "content": entry.content, "content_type": entry.content_type,
        "prompt": entry.prompt, "response": entry.response, "model_used": entry.model_used,
        "tokens_used": entry.tokens_used, "cost": entry.cost, "duration": entry.duration,
        "success": entry.success, "error_message": entry.error_message,
        "metadata": entry.metadata_ or {}, "created_at": _iso(entry.created_at),
    }


def serialize_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id, "task_id": run.task_id, "mode": run.mode, "output_format": run.output_format,
        "prompt": run.prompt, "status": run.status, "risk_level": run.risk_level,
        "country_id": run.country_id, "search_scope": run.search_scope, "memory_scope": run.memory_scope,
        "allowed_actions": run.allowed_actions or [], "plan_json": run.plan_json or [], "summary": run.summary,
        "findings": run.findings or [], "citations": run.citations or [], "artifacts": run.artifacts or [],
        "open_questions": run.open_questions or [], "actions_taken": run.actions_taken or [],
        "result_json": run.result_json or {}, "budget_tokens_total": run.budget_tokens_total,
        "budget_tokens_used": run.budget_tokens_used, "replan_count": run.replan_count,
        "search_round_count": run.search_round_count, "review_round_count": run.review_round_count,
        "step_count": run.step_count, "error_message": run.error_message, "metadata": run.metadata_ or {},
        "created_at": _iso(run.created_at), "updated_at": _iso(run.updated_at),
        "started_at": _iso(run.started_at), "ended_at": _iso(run.ended_at),
    }


def serialize_step(step: Any) -> dict[str, Any]:
    return {
        "id": step.id, "step_uuid": step.step_uuid, "run_id": step.run_id, "step_key": step.step_key,
        "step_order": step.step_order, "step_type": step.step_type, "step_name": step.step_name,
        "status": step.status, "attempt": step.attempt, "input_summary": step.input_summary,
        "output_summary": step.output_summary, "input_payload": step.input_payload or {},
        "output_payload": step.output_payload or {}, "prompt": step.prompt, "system_prompt": step.system_prompt,
        "response": step.response, "model": step.model, "provider": step.provider, "tokens": step.tokens or {},
        "duration": step.duration, "error_message": step.error_message, "metadata": step.metadata_ or {},
        "created_at": _iso(step.created_at), "updated_at": _iso(step.updated_at),
        "started_at": _iso(step.started_at), "ended_at": _iso(step.ended_at),
    }


def serialize_evidence(evidence: Any) -> dict[str, Any]:
    return {
        "id": evidence.id, "evidence_uuid": evidence.evidence_uuid, "run_id": evidence.run_id,
        "step_id": evidence.step_id, "evidence_type": evidence.evidence_type,
        "source_type": evidence.source_type, "source_name": evidence.source_name, "title": evidence.title,
        "url": evidence.url, "resolved_url": evidence.resolved_url, "content_snippet": evidence.content_snippet,
        "content_hash": evidence.content_hash, "confidence": evidence.confidence, "weight": evidence.weight,
        "metadata": evidence.metadata_ or {}, "created_at": _iso(evidence.created_at),
        "updated_at": _iso(evidence.updated_at),
    }


def serialize_conversation(conversation: Any) -> dict[str, Any]:
    return {
        "id": conversation.id, "conversation_uuid": conversation.conversation_uuid,
        "run_id": conversation.run_id, "step_id": conversation.step_id, "agent_role": conversation.agent_role,
        "phase": conversation.phase, "timestamp": _iso(conversation.timestamp), "prompt": conversation.prompt,
        "system_prompt": conversation.system_prompt, "response": conversation.response,
        "model": conversation.model, "provider": conversation.provider, "tokens": conversation.tokens or {},
        "duration": conversation.duration, "temperature": conversation.temperature,
        "metadata": conversation.metadata_ or {}, "created_at": _iso(conversation.created_at),
        "updated_at": _iso(conversation.updated_at),
    }


def serialize_memory(memory: Any) -> dict[str, Any]:
    return {
        "id": memory.id, "memory_uuid": memory.memory_uuid, "run_id": memory.run_id, "task_id": memory.task_id,
        "scope": memory.scope, "memory_type": memory.memory_type, "content": memory.content,
        "summary": memory.summary, "source_type": memory.source_type, "source_ref": memory.source_ref,
        "content_hash": memory.content_hash, "embedding": memory.embedding or [],
        "collection_name": memory.collection_name, "qdrant_point_id": memory.qdrant_point_id,
        "status": memory.status, "metadata": memory.metadata_ or {}, "created_at": _iso(memory.created_at),
        "updated_at": _iso(memory.updated_at),
    }
