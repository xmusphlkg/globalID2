"""Pure action selection and default parameter policies."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional

from src.core import get_database
from src.core.task_manager import task_manager
from src.domain import Task, TaskPriority, TaskStatus, TaskType
from src.services.agent_workflow.helpers import (
    compact_text,
    ensure_list,
    extract_keywords,
    stable_hash,
)
from src.services.agent_workflow_types import ActionRequest, ActionResult, EvidenceRef, PlanNode


DEFAULT_ALLOWED_ACTIONS = {
    "crawl_data",
    "generate_report",
    "update_disease_knowledge",
    "export_data",
}


def normalize_actions(value: Any, defaults: Iterable[str] = DEFAULT_ALLOWED_ACTIONS) -> set[str]:
    actions = {
        item.strip()
        for item in ensure_list(value)
        if isinstance(item, str) and item.strip()
    }
    return actions or set(defaults)


def looks_like_action_task(prompt: str, allowed_actions: set[str]) -> bool:
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


def infer_action_name(prompt: str, allowed_actions: set[str]) -> Optional[str]:
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
    return next((candidate for candidate in candidates if candidate in allowed_actions), None)


def infer_action_parameters(prompt: str, action: str) -> dict[str, Any]:
    if action == "generate_report":
        return {"report_type": "monthly", "language": "en", "days": 365, "enable_review": True}
    if action == "crawl_data":
        return {
            "country_code": "CN",
            "source": "all",
            "force": False,
            "process": True,
            "save_raw": True,
            "fill_missing": True,
        }
    if action == "update_disease_knowledge":
        keywords = extract_keywords(prompt, 3)
        return {
            "disease_ids": [keyword.upper() for keyword in keywords[:2]] or ["INFLUENZA"],
            "source": ["who", "wikidata", "wikipedia"],
            "force": False,
            "generator": "ai",
        }
    if action == "export_data":
        return {"country_code": "CN", "formats": ["csv", "json"], "mode": "latest"}
    return {}


def map_action_to_task_type(action: str) -> TaskType:
    mapping = {
        "crawl_data": TaskType.CRAWL_DATA,
        "generate_report": TaskType.GENERATE_REPORT,
        "update_disease_knowledge": TaskType.UPDATE_DISEASE_KNOWLEDGE,
        "export_data": TaskType.EXPORT_DATA,
    }
    try:
        return mapping[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported action: {action}") from exc


async def run_internal_action(
    *,
    task: Task,
    run: Any,
    node: PlanNode,
    allowed_actions: set[str],
    execute_action: Callable[[str, Task, dict[str, Any]], Awaitable[dict[str, Any]]],
    extract_artifacts: Callable[[dict[str, Any]], list[dict[str, Any]]],
    now: Callable[[], datetime],
    logger: Any,
) -> dict[str, Any]:
    """Execute one allow-listed action and maintain its child task lifecycle."""
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
    child_task = await task_manager.create_task(
        task_type=map_action_to_task_type(action_name),
        task_name=f"Agent action: {action_name}",
        country_id=run.country_id,
        parent_task_id=task.id,
        priority=TaskPriority.HIGH,
        input_data=dict(action_request.parameters),
        description=action_request.rationale
        or f"Executed by agent workflow step {node.step_key}",
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
            metadata={
                "parent_task_uuid": task.task_uuid,
                "action": action_name,
                "step_key": node.step_key,
            },
        )
    except Exception as log_exc:
        logger.warning(
            "Failed to add inline action start log for %s: %s",
            child_task.task_uuid,
            log_exc,
        )

    try:
        result = await execute_action(action_name, child_task, action_request.parameters)
    except Exception as exc:
        await task_manager.update_task_status(
            child_task.task_uuid, TaskStatus.FAILED, error_message=str(exc)
        )
        try:
            await task_manager.add_workbook_entry(
                child_task.task_uuid,
                entry_type="error",
                title="Inline Agent Action Failed",
                content=str(exc),
                content_type="text",
                metadata={
                    "parent_task_uuid": task.task_uuid,
                    "action": action_name,
                    "step_key": node.step_key,
                },
            )
        except Exception as log_exc:
            logger.warning(
                "Failed to add inline action failure log for %s: %s",
                child_task.task_uuid,
                log_exc,
            )
        raise

    serialized_result = json.dumps(result, ensure_ascii=False)
    action_result = ActionResult(
        action=action_name,
        success=True,
        summary=compact_text(serialized_result, 800),
        output=result,
        artifacts=extract_artifacts(result),
        evidence=[
            EvidenceRef(
                evidence_type="action",
                source_type=action_name,
                source_name=child_task.task_name,
                title=child_task.task_name,
                content_snippet=compact_text(serialized_result, 800),
                content_hash=stable_hash(
                    json.dumps(result, sort_keys=True, ensure_ascii=False)
                ),
                confidence=0.9,
                metadata={
                    "child_task_uuid": child_task.task_uuid,
                    "action": action_name,
                },
            )
        ],
        metadata={"child_task_uuid": child_task.task_uuid, "action": action_name},
    )

    async with get_database() as child_db:
        child_db_task = await child_db.get(Task, child_task.id)
        if child_db_task is not None:
            child_db_task.output_data = result
            child_db_task.status = TaskStatus.COMPLETED
            child_db_task.completed_at = now()
            await child_db.commit()

    await task_manager.update_task_status(child_task.task_uuid, TaskStatus.COMPLETED)
    try:
        await task_manager.add_workbook_entry(
            child_task.task_uuid,
            entry_type="success",
            title="Inline Agent Action Completed",
            content=compact_text(serialized_result, 1200),
            content_type="json",
            metadata={
                "parent_task_uuid": task.task_uuid,
                "action": action_name,
                "step_key": node.step_key,
            },
        )
    except Exception as log_exc:
        logger.warning(
            "Failed to add inline action completion log for %s: %s",
            child_task.task_uuid,
            log_exc,
        )

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
