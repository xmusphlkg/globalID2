"""Business submissions and targeted durable completion delivery."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from sqlalchemy import select

from src.core import get_database
from src.domain import Task
from src.domain.ai_work_item import AIWorkItem

from .context import _set_waiting, current_parent, parent_execution_generation
from .contracts import (
    TERMINAL_STATUSES,
    AIWorkFailed,
    RoutingRequirements,
    parent_cancelled,
)
from .payloads import request_digest, write_request
from .signals import activity_signals
from .store import _lock_reservation, request_cancellation, submit_activity


def _require_current_generation(parent, state) -> None:
    if parent is not None and parent_execution_generation(parent) != state.generation:
        from src.services.exceptions import TaskExecutionSupersededError

        raise TaskExecutionSupersededError(
            "Parent task was explicitly retried with a new execution generation"
        )


async def wait_for_activities(
    ids, *, parent_task_uuid: str, timeout: float = 2
) -> None:
    """Wait for any registered activity; bounded reconciliation survives lost hints."""
    ids = tuple(ids)
    if not ids:
        return
    async with activity_signals(get_database).subscribe(ids, parent_task_uuid) as event:
        async with get_database() as db:
            terminal = await db.scalar(
                select(AIWorkItem.id)
                .where(AIWorkItem.id.in_(ids), AIWorkItem.status.in_(TERMINAL_STATUSES))
                .limit(1)
            )
            parent = await db.scalar(
                select(Task).where(Task.task_uuid == parent_task_uuid)
            )
        if terminal is not None or parent_cancelled(
            parent, parent_task_uuid=parent_task_uuid
        ):
            return
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            pass


async def _await_result(work_id: int, state) -> dict:
    async with state.lock:
        state.waiting += 1
        if state.waiting == 1:
            await _set_waiting(state, True)
    try:
        async with activity_signals(get_database).subscribe(
            [work_id], state.task_uuid
        ) as event:
            while True:
                event.clear()
                async with get_database() as db:
                    item = await db.get(AIWorkItem, work_id)
                    # Read parent cancellation after the activity result, so a
                    # cancellation committed while the result was loading wins.
                    parent = await db.scalar(
                        select(Task)
                        .where(Task.task_uuid == state.task_uuid)
                        .with_for_update()
                    )
                    _require_current_generation(parent, state)
                    if parent_cancelled(parent, parent_task_uuid=state.task_uuid):
                        if item is not None and item.status not in TERMINAL_STATUSES:
                            await request_cancellation(db, item)
                        status, result, error = (
                            "cancelled",
                            None,
                            "Parent task cancelled",
                        )
                    elif item is None:
                        status, result, error = "failed", None, "Activity disappeared"
                    else:
                        status, result, error = item.status, item.result, item.error
                if parent_cancelled(parent, parent_task_uuid=state.task_uuid):
                    from src.services.exceptions import TaskCancelledError

                    raise TaskCancelledError(
                        "Parent task cancelled while awaiting model work"
                    )
                if status == "succeeded":
                    return result or {}
                if status in {"failed", "cancelled"}:
                    message = error or f"Activity {work_id} {status}"
                    state.failures.append(message)
                    raise AIWorkFailed(message)
                await event.wait()
    finally:
        async with state.lock:
            state.waiting = max(0, state.waiting - 1)
            if state.waiting == 0:
                await _set_waiting(state, False)


async def _enqueue(state, *, line, payload, capability) -> int:
    """Caller holds enqueue_lock; the parent row fences concurrent explicit retries."""
    async with get_database() as db:
        await _lock_reservation(db, f"parent:{state.task_uuid}")
        parent = await db.scalar(
            select(Task).where(Task.task_uuid == state.task_uuid).with_for_update()
        )
        if parent_cancelled(parent, parent_task_uuid=state.task_uuid):
            from src.services.exceptions import TaskCancelledError

            raise TaskCancelledError("Parent task cancelled before model submission")
        _require_current_generation(parent, state)
        item = await submit_activity(
            db,
            parent_task_uuid=state.task_uuid,
            line=line,
            priority=state.priority,
            payload=payload,
            capability=capability,
        )
        work_id = item.id
    state.work_ids.add(work_id)
    return work_id


async def _submit(state, *, line, payload, capability):
    async with state.enqueue_lock:
        work_id = await _enqueue(
            state, line=line, payload=payload, capability=capability
        )
    return await _await_result(work_id, state)


async def submit_completion(
    agent: Any, prompt: str, system: str | None, use_cache: bool, kwargs: dict[str, Any]
) -> str:
    state = current_parent()
    if state is None:
        raise RuntimeError(
            "Model work requires a queued parent task; run it through the task worker"
        )
    from src.ai.attachments import Attachment

    options = dict(kwargs)
    routing = options.pop("routing", None) or RoutingRequirements()
    if not isinstance(routing, RoutingRequirements):
        raise TypeError("routing must be RoutingRequirements")
    generation = {
        "agent_name": agent.name,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "use_cache": use_cache,
    }
    content = {
        "prompt": prompt,
        "system": system,
        "kwargs": options,
        "generation": generation,
        "routing": asdict(routing),
        "execution_generation": state.generation,
    }
    attachments = options.pop("attachments", None)
    if attachments:
        import base64

        if any(not isinstance(a, Attachment) for a in attachments):
            raise TypeError("attachments must contain Attachment objects")
        content["attachments"] = [
            {
                "name": a.name,
                "text": a.text,
                "media_type": a.media_type,
                "data": base64.b64encode(a.data).decode() if a.data else None,
            }
            for a in attachments
        ]
    invocation_key = await asyncio.to_thread(request_digest, content)
    line = (
        "report_review"
        if state.line == "report_section" and "review" in agent.name.lower()
        else state.line
    )
    async with state.enqueue_lock:
        ordinal = state.invocations.get(invocation_key, 0)
        content["invocation"] = ordinal
        path, digest = await asyncio.to_thread(write_request, state.task_uuid, content)
        payload = {
            "request_path": path,
            "request_key": digest,
            **generation,
            **asdict(routing),
            "execution_generation": state.generation,
            "invocation": ordinal,
            # A repeated semantic attempt or an explicit retry must be able to
            # replace an invalid answer. Crash replay still reuses its activity.
            "use_cache": bool(use_cache and ordinal == 0 and state.generation == 0),
        }
        work_id = await _enqueue(
            state, line=line, payload=payload, capability=routing.capability
        )
        # A failed enqueue must not consume an ordinal that has no durable work.
        state.invocations[invocation_key] = ordinal + 1
    result = await _await_result(work_id, state)
    if result.get("conversation"):
        agent.conversation_history.append(result["conversation"])
    agent._last_input_mode = result.get("input_mode")
    return str(result.get("text") or "")


async def submit_probe(operation: str, arguments: dict[str, Any]) -> Any:
    state = current_parent()
    if state is None:
        raise RuntimeError("Health checks require a worker parent operation")
    result = await _submit(
        state,
        line="model_probe",
        capability="health",
        payload={
            "operation": operation,
            "arguments": arguments,
            "execution_generation": state.generation,
        },
    )
    return result.get("operation_result")
