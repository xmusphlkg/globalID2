"""Worker operations for API/scheduler actions that require model work."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from src.core import get_database
from src.core.task_manager import task_manager
from src.domain import Task, TaskPriority, TaskStatus, TaskType

NOTIFICATION_LOCALE_NAMES = {
    "en": "English",
    "zh": "Simplified Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}


async def _translate_notification(arguments: dict[str, Any]) -> dict[str, Any]:
    from src.ai.agents.workflow import WorkflowAgent
    from src.domain import AIProviderConfig

    system = (
        "You translate administrative product-update emails for GIDS. "
        "Preserve Markdown structure, links, numbers, code blocks, disease names, and dates. "
        "Do not add facts, disclaimers, or sections. Return only valid JSON."
    )
    prompt = json.dumps(
        {
            "source_locale": arguments["source_locale"],
            "target_locales": {
                locale: NOTIFICATION_LOCALE_NAMES.get(locale, locale)
                for locale in arguments["target_locales"]
            },
            "subject": arguments["subject"],
            "markdown": arguments["markdown"],
            "required_schema": {
                "translations": {
                    "<locale>": {
                        "subject": "translated subject",
                        "markdown": "translated markdown",
                    }
                }
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = WorkflowAgent(
        name="NotificationTranslation",
        system_prompt=system,
        temperature=0.1,
        max_tokens=5000,
    )
    # BaseAgent.complete submits an ai_work_item under this AGENT_WORKFLOW parent.
    # Only the dispatcher chooses the route and enters provider execution.
    text = await agent.complete(prompt=prompt, system=system, use_cache=False)
    conversation = agent.get_latest_conversation() or {}
    runtime_route = (conversation.get("metadata") or {}).get("runtime_route") or {}
    model_route = {
        key: runtime_route.get(key)
        for key in ("model_id", "model_key", "model_name", "provider_key", "provider_name")
    }
    # Telemetry identifies the actual dispatched route; enrich only its display name.
    if runtime_route.get("provider_id") is not None:
        async with get_database() as db:
            provider = await db.get(AIProviderConfig, runtime_route["provider_id"])
            if provider is not None:
                model_route["provider_name"] = provider.provider_name
    return {"text": text, "model_route": model_route}


async def enqueue_model_operation(
    operation: str, arguments: dict[str, Any], *, priority=TaskPriority.HIGH
) -> Task:
    # Repeated scheduler ticks attach to active work instead of growing a second backlog.
    async with get_database() as db:
        candidates = (
            (
                await db.execute(
                    select(Task).where(
                        Task.task_type == TaskType.AGENT_WORKFLOW,
                        Task.status.in_(
                            [
                                TaskStatus.PENDING,
                                TaskStatus.QUEUED,
                                TaskStatus.RUNNING,
                                TaskStatus.RETRYING,
                            ]
                        ),
                        Task.input_data["operation"].as_string() == operation,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = next(
            (
                t
                for t in candidates
                if (t.input_data or {}).get("arguments") == arguments
            ),
            None,
        )
        if existing:
            return existing
    # Operation arguments contain ids and options; prompts are constructed by the worker.
    return await task_manager.create_task(
        task_type=TaskType.AGENT_WORKFLOW,
        task_name=f"Model operation: {operation}",
        status=TaskStatus.QUEUED,
        priority=priority,
        input_data={"operation": operation, "arguments": arguments},
    )


async def wait_task_output(task_uuid: str) -> dict[str, Any]:
    while True:
        current = await task_manager.get_task_by_uuid(task_uuid)
        if current is None:
            raise ValueError(f"Task not found: {task_uuid}")
        if current.status == TaskStatus.COMPLETED:
            return current.output_data or {}
        if current.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise RuntimeError(current.last_error or f"Worker task {task_uuid} failed")
        await asyncio.sleep(0.5)


async def run_worker_operation(operation: str, arguments: dict[str, Any]) -> Any:
    task = await enqueue_model_operation(operation, arguments)
    return (await wait_task_output(task.task_uuid)).get("result")


async def execute_model_operation(operation: str, arguments: dict[str, Any]) -> Any:
    if operation == "notification_translation":
        return await _translate_notification(arguments)
    if operation == "disease_audit":
        from src.services.disease_duplicate_audit_service import (
            DiseaseDuplicateAuditService,
        )

        paths = {
            key: arguments[key]
            for key in ("standard_path", "mapping_dir")
            if arguments.get(key)
        }
        from pathlib import Path

        service = DiseaseDuplicateAuditService(
            **{key: Path(value) for key, value in paths.items()}
        )
        audit = await asyncio.to_thread(
            service.run_local_audit,
            include_new_disease_candidates=arguments.get(
                "include_new_disease_candidates", True
            ),
        )
        if arguments.get("include_ai", True):
            audit["ai_review"] = await service.run_ai_review(
                audit, max_candidates=arguments.get("max_ai_candidates", 40)
            )
        return audit
    if operation == "content_governance":
        from src.services.ai_content_governance_service import (
            ai_content_governance_service,
        )

        async with get_database() as db:
            return await ai_content_governance_service.run_once(db, **arguments)
    if operation == "mapping_suggest":
        from src.services.disease_mapping_ai_service import disease_mapping_ai_service

        async with get_database() as db:
            return await disease_mapping_ai_service.suggest_for_category(
                db, int(arguments["category_id"])
            )
    if operation == "weekly_ai_review":
        from pathlib import Path

        from src.literature.weekly_ai_review import review_weekly_brief_files

        options = dict(arguments)
        options["weekly_dir"] = Path(options["weekly_dir"])
        options["registry_path"] = Path(options["registry_path"])
        return await review_weekly_brief_files(**options)
    if operation == "governance_stream":
        from src.services.ai_content_governance_service import (
            ai_content_governance_service,
        )

        return await ai_content_governance_service.stream_review(**arguments)
    if operation == "governance_simple":
        from src.services.ai_content_governance_service import (
            ai_content_governance_service,
        )

        method = arguments["method"]
        if method not in {"review_knowledge_sources", "review_learning_suggestions"}:
            raise ValueError("Unsupported governance lane")
        async with get_database() as db:
            return await getattr(ai_content_governance_service, method)(
                db, limit=arguments.get("limit", 64)
            )
    if operation == "idle_situation":
        from src.ai.work_queue import current_parent
        from src.services.situation_notices.runner import run_situation

        # Reuse the same collect/organize/store and dirty-marking contract.
        return await run_situation(
            fetch=False,
            document_limit=arguments.get("document_limit", 40),
            ai_limit=arguments.get("ai_limit", 30),
            task_uuid=current_parent().task_uuid,
        )
    if operation in {"check_model", "check_provider", "check_all_models"}:
        from src.ai.work_queue import submit_probe

        return await submit_probe(operation, arguments)
    raise ValueError(f"Unsupported worker model operation: {operation}")
