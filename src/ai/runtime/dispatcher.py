"""Shared durable scheduler with bounded retries and renewable fenced leases."""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from src.ai.catalogue.errors import classify_failure
from src.ai.providers import ProviderTransport
from src.ai.route_selection import route_weight
from src.core import get_database, get_logger
from src.domain import Task
from src.domain.ai_work_item import AIWorkItem

from .context import _DISPATCHING
from .contracts import LEASE_SECONDS, MAX_ATTEMPTS, activity_cancelled
from .payloads import read_request
from .progress import SuccessProgressBuffer, _work_event, note_parent_work
from .signals import activity_signals, publish_changes
from .store import (
    CLAIM_BATCH_SIZE,
    _now,
    _owns_lease,
    _utc,
    active_route_lease_counts,
    claim_owned_cancellations,
    claim_probe,
    claim_work_batch,
    clear_lease,
    enqueue_runtime_probe,
    expire_queued_activities,
    finish_cancelled_lease,
    lock_activity,
    recover_expired_leases,
    release_owned_leases,
    renew_owned_leases,
)

logger = get_logger(__name__)


class AIWorkDispatcher:
    def __init__(self, route_loader=None, complete=None, *, owner: str | None = None):
        if route_loader is None:
            from src.ai.model_center import get_runtime_routes

            route_loader = get_runtime_routes
        self.route_loader = route_loader
        self.complete = complete
        self.owner = owner or str(uuid4())
        self.transport = ProviderTransport()
        self.active: dict[int, asyncio.Task] = {}
        self.leases: dict[int, AIWorkItem] = {}
        self.outcomes: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.progress = SuccessProgressBuffer()
        self._tick_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._renewed_at = 0.0
        self._queue_checked_at: float | None = None

    async def _execute(self, item: AIWorkItem, route: dict[str, Any]) -> None:
        token = _DISPATCHING.set(True)
        succeeded, noted = False, None
        attempted = False
        try:
            # Cancellation is checked before spending provider capacity and again
            # in the terminal transaction. A late response cannot revive work.
            async with get_database() as db:
                current = await db.get(AIWorkItem, item.id)
                if item.lease_owner != self.owner or not _owns_lease(current, item):
                    return
                parent = await db.scalar(
                    select(Task).where(Task.task_uuid == item.parent_task_uuid)
                )
                cancelled = activity_cancelled(parent, current)
            if cancelled:
                result = None
            elif self.complete:
                attempted = True
                result = await self.complete(item, route)
                result = {"text": result} if isinstance(result, str) else result
            else:
                attempted = True
                result = await self._complete(item, route)
            async with get_database() as db:
                current = await lock_activity(db, item.id)
                if not _owns_lease(current, item):
                    return
                parent = await db.scalar(
                    select(Task).where(Task.task_uuid == item.parent_task_uuid)
                )
                cancelled = cancelled or activity_cancelled(parent, current)
                current.status = "cancelled" if cancelled else "succeeded"
                current.result = None if cancelled else result
                current.error = None
                current.finished_at = _now()
                clear_lease(current)
                await publish_changes(db, work_ids=[item.id])
                noted = _work_event(
                    current, route_key=item.route_key, record_error=False
                )
            succeeded = not cancelled
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = classify_failure(exc)
            async with get_database() as db:
                current = await lock_activity(db, item.id)
                if not _owns_lease(current, item):
                    return
                parent = await db.scalar(
                    select(Task).where(Task.task_uuid == item.parent_task_uuid)
                )
                cancelled = activity_cancelled(parent, current)
                current.error = (
                    None if cancelled else f"{type(exc).__name__}: {str(exc)[:1000]}"
                )
                clear_lease(current)
                current.route_key = None
                # A route-scoped permanent failure can fail over to another route;
                # malformed requests and unknown programming errors fail immediately.
                retry = failure.retryable or failure.scope != "request"
                if cancelled:
                    current.status, current.finished_at = "cancelled", _now()
                elif retry and current.attempt < MAX_ATTEMPTS:
                    current.status = "queued"
                    current.attempt += 1
                    current.payload = {
                        **(current.payload or {}),
                        "last_failed_route": item.route_key,
                    }
                    # Retry-After is a lower bound, including edge blocks that
                    # failed before the provider API handled the request.
                    delay = max(
                        min(60.0, 2 ** (current.attempt - 1)),
                        failure.retry_after_seconds or 0,
                    ) + random.random()
                    current.available_at = _now() + timedelta(seconds=delay)
                else:
                    current.status, current.finished_at = "failed", _now()
                await publish_changes(db, work_ids=[item.id])
                noted = _work_event(
                    current, route_key=item.route_key, record_error=not cancelled
                )
        finally:
            if attempted and noted is not None and noted["status"] != "cancelled":
                self.outcomes[str(route.get("model_key"))].append(succeeded)
            _DISPATCHING.reset(token)
            self._wake.set()
            if noted is not None:
                activity_signals(get_database).notify([item.id])
                if noted["status"] == "succeeded" and not noted["record_error"]:
                    self.progress.mark(noted)
                else:
                    await note_parent_work(noted)

    async def _complete(
        self, item: AIWorkItem, route: dict[str, Any]
    ) -> dict[str, Any]:
        from src.ai.agents.base import BaseAgent

        class WorkerAgent(BaseAgent):
            async def process(self, **kwargs):
                raise NotImplementedError

        payload = item.payload
        if item.capability == "health":
            from src.ai.model_center import (
                check_all_models,
                check_model_by_id,
                check_provider_by_id,
                probe_degraded_model_routes,
            )

            operations = {
                "check_model": check_model_by_id,
                "check_provider": check_provider_by_id,
                "check_all_models": check_all_models,
                "probe_degraded": probe_degraded_model_routes,
            }
            operation = operations[payload["operation"]]
            return {
                "operation_result": await operation(**(payload.get("arguments") or {}))
            }
        content = await asyncio.to_thread(read_request, payload["request_path"])
        kwargs = dict(content.get("kwargs") or {})
        if content.get("attachments"):
            import base64

            from src.ai.attachments import Attachment

            kwargs["attachments"] = [
                Attachment(
                    name=a["name"],
                    text=a["text"],
                    media_type=a["media_type"],
                    data=base64.b64decode(a["data"]) if a.get("data") else None,
                )
                for a in content["attachments"]
            ]
        kwargs.update(
            model_request_timeout_seconds=min(
                float(kwargs.get("model_request_timeout_seconds") or 840), 840
            ),
        )
        agent = WorkerAgent(
            name=payload.get("agent_name") or item.line,
            temperature=payload.get("temperature", 0.1),
            max_tokens=payload.get("max_tokens", 2400),
        )
        agent.transport = self.transport
        text = await agent.complete(
            content["prompt"],
            system=content.get("system"),
            use_cache=payload.get("use_cache", True),
            _scheduled_route=route,
            **kwargs,
        )
        return {
            "text": text,
            "conversation": agent.get_latest_conversation(),
            "input_mode": getattr(agent, "_last_input_mode", None),
        }

    async def tick(self) -> int:
        async with self._tick_lock:
            claimed = await self._tick()
            await self.progress.flush()
            return claimed

    async def _tick(self) -> int:
        now = asyncio.get_running_loop().time()
        to_cancel: set[asyncio.Task] = set()
        requested = await claim_owned_cancellations(self.owner) if self.active else []
        for item in requested:
            task = self.active.get(item.id)
            if task is not None and not task.done():
                to_cancel.add(task)
        if self.active and now - self._renewed_at >= LEASE_SECONDS / 3:
            owned = await renew_owned_leases(self.owner)
            to_cancel.update(
                task
                for work_id, task in self.active.items()
                if work_id not in owned and not task.done()
            )
            self._renewed_at = now
        for work_id, task in self.active.items():
            lease = self.leases.get(work_id)
            if (
                lease
                and lease.deadline_at
                and _utc(lease.deadline_at) <= _now()
                and not task.done()
            ):
                to_cancel.add(task)
        for task in to_cancel:
            task.cancel()
        # Cancellation must finish before an expired activity can be claimed
        # again. Otherwise _start can replace an active task with the same id
        # while the old provider request is still cleaning up.
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
        for item in requested:
            # Only this process knows that its provider task has stopped.
            task = self.active.get(item.id)
            if task is not None and task.done():
                await finish_cancelled_lease(item)
        for work_id, task in list(self.active.items()):
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Activity executor failed; its lease will expire")
                del self.active[work_id]
                self.leases.pop(work_id, None)
        await recover_expired_leases()
        if self._queue_checked_at is None or now - self._queue_checked_at >= 20:
            await expire_queued_activities()
            self._queue_checked_at = now
        while (probe := await claim_probe(owner=self.owner)) is not None:
            self._start(probe, {"model_key": "health:probe"})
        routes = [
            r
            for r in await self.route_loader()
            if r.get("available_for_routing") and r.get("runtime_model_capacity", 0) > 0
        ]
        counts = defaultdict(int, await active_route_lease_counts()) if routes else {}
        claimed = 0
        while routes:
            weights = [
                route_weight(
                    int(r["runtime_model_capacity"]),
                    counts[r["model_key"]],
                    self.outcomes[r["model_key"]],
                )
                for r in routes
            ]
            if not any(weights):
                break
            route = random.choices(routes, weights=weights)[0]
            idle = {
                r["model_key"]: r.get("capabilities") or ["text"]
                for r, weight in zip(routes, weights)
                if weight > 0
            }
            items = await claim_work_batch(
                route,
                owner=self.owner,
                alternatives=idle,
                limit=min(
                    CLAIM_BATCH_SIZE,
                    int(route["runtime_model_capacity"]) - counts[route["model_key"]],
                ),
            )
            if not items:
                routes.remove(route)
                continue
            for item in items:
                self._start(item, route)
            counts[route["model_key"]] += len(items)
            claimed += len(items)
        return claimed

    def _start(self, item, route):
        self.leases[item.id] = item
        self.active[item.id] = asyncio.create_task(
            self._execute(item, route), name=f"ai-activity-{item.id}"
        )

    async def _idle(self, stop_event, timeout=0.5):
        waiters = {
            asyncio.create_task(self._wake.wait()),
            asyncio.create_task(stop_event.wait()),
        }
        try:
            await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def run(self, stop_event: asyncio.Event) -> None:
        last_probe = 0.0
        try:
            while not stop_event.is_set():
                try:
                    now = asyncio.get_running_loop().time()
                    if now - last_probe >= 60:
                        await enqueue_runtime_probe()
                        last_probe = now
                    self._wake.clear()
                    await self.tick()
                except Exception:
                    logger.exception("Model activity dispatch scan failed")
                await self._idle(stop_event)
        finally:
            for task in self.active.values():
                task.cancel()
            await asyncio.gather(*self.active.values(), return_exceptions=True)
            try:
                await release_owned_leases(self.owner)
            finally:
                try:
                    await self.transport.close()
                finally:
                    # UI projection cannot hold up provider/lease cleanup.
                    # Remaining hints are reconstructible from durable counts.
                    try:
                        async with asyncio.timeout(0.5):
                            await self.progress.flush(force=True)
                    except TimeoutError:
                        pass
