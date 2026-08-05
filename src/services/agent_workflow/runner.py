"""Execution state machine for agent workflows.

The runner owns orchestration and state transitions. Domain-specific operations
remain service callbacks so existing extension and test seams stay compatible.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from src.domain import Task
from src.services.exceptions import TaskCancelledError


async def execute_workflow(
    service: Any,
    task: Task,
    *,
    database_factory: Callable[[], Any],
    task_manager: Any,
    now: Callable[[], datetime],
    compact_text: Callable[[Any, int], str],
    stable_hash: Callable[[Any], str],
    logger: Any,
) -> dict[str, Any]:
    """Run or resume one workflow while preserving persisted step progress."""
    payload = dict(task.input_data or {})
    prompt = str(payload.get("prompt") or task.description or task.task_name or "").strip()
    if not prompt:
        raise ValueError("AGENT_WORKFLOW tasks require a prompt")

    allowed_actions = service._normalize_actions(payload.get("allowed_actions"))
    search_scope = str(payload.get("search_scope") or "web+db+memory")
    memory_scope = str(payload.get("memory_scope") or "project")
    output_format = str(payload.get("output_format") or "evidence_report")
    country_id = service._coerce_int(payload.get("country_id") or task.country_id)
    mode = str(payload.get("mode") or "research")

    async with database_factory() as db:
        run = None
        final_output = None
        try:
            run = await service._get_or_create_run(
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

            await _log_started(
                task_manager,
                task,
                prompt=prompt,
                mode=mode,
                search_scope=search_scope,
                memory_scope=memory_scope,
                allowed_actions=allowed_actions,
                compact_text=compact_text,
                logger=logger,
            )
            plan_nodes = await service._ensure_plan(
                db, run, task, prompt, payload, search_scope, allowed_actions
            )
            completed_steps = await service._load_completed_steps(db, run.id)
            context = service._build_initial_context(
                prompt=prompt,
                payload=payload,
                search_scope=search_scope,
                memory_scope=memory_scope,
            )
            context["plan"] = [node.to_dict() for node in plan_nodes]
            await db.commit()

            await _run_steps(
                service,
                db,
                task,
                run,
                plan_nodes,
                completed_steps,
                context,
                allowed_actions,
                prompt,
                task_manager,
            )

            final_output = await service._build_final_output(
                db, run, task, prompt, context, plan_nodes
            )
            _complete_run(
                service,
                run,
                task,
                final_output,
                prompt=prompt,
                search_scope=search_scope,
                memory_scope=memory_scope,
                allowed_actions=allowed_actions,
                plan_nodes=plan_nodes,
                completed_steps=completed_steps,
                stable_hash=stable_hash,
                now=now,
            )
            await db.commit()

            try:
                await service._store_workflow_memory(db, run, task, prompt, final_output)
                await db.commit()
            except Exception as memory_exc:
                logger.warning(
                    "Workflow memory persistence skipped for %s: %s",
                    task.task_uuid,
                    memory_exc,
                )
        except TaskCancelledError as exc:
            if run is not None:
                run.status = "cancelled"
                run.error_message = str(exc)
                run.ended_at = now()
                run.metadata_ = {
                    **(run.metadata_ or {}),
                    "cancelled": True,
                    "cancel_reason": str(exc),
                }
                await db.commit()
            await _log_terminal(task_manager, task, exc, cancelled=True, logger=logger)
            raise
        except Exception as exc:
            if run is not None:
                run.status = "failed"
                run.error_message = str(exc)
                run.ended_at = now()
                run.metadata_ = {**(run.metadata_ or {}), "failed": True, "error": str(exc)}
                await db.commit()
            await _log_terminal(task_manager, task, exc, cancelled=False, logger=logger)
            raise

    await _log_completed(task_manager, task, final_output, compact_text, logger)
    return final_output.to_dict() if final_output else {}


async def _run_steps(
    service: Any,
    db: Any,
    task: Task,
    run: Any,
    plan_nodes: list[Any],
    completed_steps: dict[str, Any],
    context: dict[str, Any],
    allowed_actions: set[str],
    prompt: str,
    task_manager: Any,
) -> None:
    i = 0
    while i < len(plan_nodes):
        if await task_manager.is_cancel_requested(task.task_uuid):
            raise TaskCancelledError("Cancellation requested by user")

        node = plan_nodes[i]
        step = completed_steps.get(node.step_key)
        if step and str(step.status) == "completed":
            context.update(
                service._merge_step_context(
                    context, step.output_payload or {}, step.step_type
                )
            )
            i += 1
            continue

        step = await service._start_step(db, run, node)
        try:
            result = await service._execute_node(
                db=db,
                task=task,
                run=run,
                node=node,
                context=context,
                allowed_actions=allowed_actions,
                prompt=prompt,
            )
            await service._finish_step(db, run, step, result)
            await db.commit()
            completed_steps[node.step_key] = step
            context.update(
                service._merge_step_context(
                    context, result["output_payload"], node.step_type
                )
            )
            await service._refresh_run_progress(
                db, run, task, completed_steps_count=len(completed_steps)
            )
            await db.commit()

            if node.step_type == "review" and not result["output_payload"].get("approved", False):
                follow_up_nodes = service._maybe_schedule_replan_nodes(
                    run=run,
                    node=node,
                    result=result["output_payload"],
                    context=context,
                )
                if follow_up_nodes:
                    plan_nodes.extend(follow_up_nodes)
                    context["plan"] = [item.to_dict() for item in plan_nodes]
                    await service._persist_plan(db, run, plan_nodes)
                    await db.commit()

            if node.step_type == "finalize":
                break
        except Exception as exc:
            await service._fail_step(db, run, step, exc)
            await db.commit()
            raise

        i += 1


def _complete_run(
    service: Any,
    run: Any,
    task: Task,
    final_output: Any,
    *,
    prompt: str,
    search_scope: str,
    memory_scope: str,
    allowed_actions: set[str],
    plan_nodes: list[Any],
    completed_steps: dict[str, Any],
    stable_hash: Callable[[Any], str],
    now: Callable[[], datetime],
) -> None:
    run.status = "completed"
    run.summary = final_output.summary
    run.findings = final_output.findings
    run.citations = final_output.citations
    run.actions_taken = final_output.actions_taken
    run.artifacts = final_output.artifacts
    run.open_questions = final_output.open_questions
    run.result_json = final_output.to_dict()
    run.step_count = len(
        [step for step in completed_steps.values() if str(step.status) == "completed"]
    )
    run.budget_tokens_total = service.total_token_budget
    run.budget_tokens_used = service._aggregate_tokens(
        [step.tokens for step in completed_steps.values()]
    )
    run.ended_at = now()
    run.metadata_ = {
        **(run.metadata_ or {}),
        "task_uuid": task.task_uuid,
        "prompt_hash": stable_hash(prompt),
        "search_scope": search_scope,
        "memory_scope": memory_scope,
        "allowed_actions": sorted(allowed_actions),
        "plan_steps": len(plan_nodes),
        "step_count": run.step_count,
    }


async def _log_started(
    task_manager: Any,
    task: Task,
    *,
    prompt: str,
    mode: str,
    search_scope: str,
    memory_scope: str,
    allowed_actions: set[str],
    compact_text: Callable[[Any, int], str],
    logger: Any,
) -> None:
    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="info",
            title="Agent Workflow Started",
            content=(
                f"Prompt: {compact_text(prompt, 800)}\n"
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


async def _log_terminal(
    task_manager: Any,
    task: Task,
    exc: Exception,
    *,
    cancelled: bool,
    logger: Any,
) -> None:
    state = "Cancelled" if cancelled else "Failed"
    entry_type = "warning" if cancelled else "error"
    metadata_key = "cancelled" if cancelled else "failed"
    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type=entry_type,
            title=f"Agent Workflow {state}",
            content=str(exc),
            content_type="text",
            metadata={"task_uuid": task.task_uuid, metadata_key: True},
        )
    except Exception as log_exc:
        logger.warning(
            "Failed to add workflow %s log for %s: %s",
            state.lower(),
            task.task_uuid,
            log_exc,
        )


async def _log_completed(
    task_manager: Any,
    task: Task,
    final_output: Any,
    compact_text: Callable[[Any, int], str],
    logger: Any,
) -> None:
    try:
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="success",
            title="Agent Workflow Completed",
            content=compact_text(json.dumps(final_output.to_dict(), ensure_ascii=False), 1500),
            content_type="json",
            metadata={
                "task_uuid": task.task_uuid,
                "evidence_count": final_output.evidence_count,
                "step_count": final_output.step_count,
                "risk_level": final_output.risk_level,
            },
        )
    except Exception as log_exc:
        logger.warning("Failed to add workflow completion log for %s: %s", task.task_uuid, log_exc)
