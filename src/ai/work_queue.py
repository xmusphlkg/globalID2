"""Durable model work and the worker-owned, capacity-aware dispatcher.

Business code awaits durable results; only ``AIWorkDispatcher`` enters the
provider execution context. Waiting coroutines retain their business stack but
hold neither ordinary-task nor model admission permits. Restarted parents replay
completed calls and attach to pending calls by their content-addressed reference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import tempfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select, text, tuple_

from src.ai.task_progress import displayed_progress, task_type_name
from src.core import get_database, get_logger
from src.domain import Task, TaskStatus, TaskWorkbook
from src.domain.ai_work_item import AIWorkItem

from .route_selection import route_weight

logger = get_logger(__name__)
LEASE_SECONDS = 15 * 60
MAX_ATTEMPTS = 3
CLAIM_SCAN_PAGE_SIZE = 64
WAIT_POLL_SECONDS = 0.5
# None means individual failures are tolerated and counted by the line's organizer.
FAILURE_THRESHOLDS = {
    "situation_notice": None,
    "knowledge_brief": 0,
    "literature_enrich": 0,
    "report_section": 0,
    "report_review": 0,
}
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
ROOT = Path(__file__).resolve().parents[2]


class AIWorkFailed(RuntimeError):
    pass


@dataclass
class ParentContext:
    task_uuid: str
    line: str
    priority: str
    waiting: int = 0
    failures: list[str] = field(default_factory=list)
    work_ids: set[int] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_checked_at: float = 0.0
    cancel_requested: bool = False


_PARENT: ContextVar[ParentContext | None] = ContextVar("ai_work_parent", default=None)
_DISPATCHING: ContextVar[bool] = ContextVar("ai_work_dispatching", default=False)
_WAITING: set[str] = set()


def in_dispatcher() -> bool:
    return _DISPATCHING.get()


def require_dispatcher() -> None:
    """Provider transports run only inside an admitted worker execution."""
    if not in_dispatcher():
        raise RuntimeError("Model transport requires the worker dispatcher")


def current_parent() -> ParentContext | None:
    return _PARENT.get()


def waiting_parent_uuids() -> set[str]:
    return set(_WAITING)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def parent_line(task: Task) -> str:
    if (getattr(task, "input_data", None) or {}).get("operation") == "idle_situation":
        return "situation_notice"
    return {
        "generate_report": "report_section",
        "generate_section": "report_section",
        "review_section": "report_review",
        "update_disease_knowledge": "knowledge_brief",
        "enrich_literature": "literature_enrich",
        "refresh_situation_sources": "situation_notice",
    }.get(_value(task.task_type), _value(task.task_type))


@asynccontextmanager
async def task_context(task: Task):
    existing = current_parent()
    if existing and existing.task_uuid == task.task_uuid:
        yield existing
        return
    line = parent_line(task)
    priority = _value(getattr(task, "priority", "normal") or "normal")
    inp = getattr(task, "input_data", None) or {}
    if line in {"report_section", "report_review"} and priority != "urgent":
        priority = "high"
    if line == "knowledge_brief" and (
        "disease-knowledge-autopilot"
        in {
            str(inp.get("requested_by") or "").strip().casefold(),
            str(inp.get("initiated_via") or "").strip().casefold(),
        }
        or inp.get("autopilot")
        or inp.get("trigger") == "autopilot"
        or inp.get("trigger_source") == "autopilot"
    ):
        priority = "low"
    state = ParentContext(task.task_uuid, line, priority)
    token = _PARENT.set(state)
    try:
        yield state
    finally:
        _PARENT.reset(token)
        if state.waiting or task.task_uuid in _WAITING:
            await _set_waiting(state, False)


async def _set_waiting(state: ParentContext, waiting: bool) -> None:
    async with get_database() as db:
        await _lock_reservation(db, f"parent:{state.task_uuid}")
        task = (
            await db.execute(
                select(Task).where(Task.task_uuid == state.task_uuid).with_for_update()
            )
        ).scalar_one_or_none()
        if task is not None:
            data = dict(task.input_data or {})
            data["ai_waiting"] = waiting
            task.input_data = data
    if waiting:
        _WAITING.add(state.task_uuid)
    else:
        _WAITING.discard(state.task_uuid)


async def _parent_cancel_requested(
    db, state: ParentContext, *, refresh: bool = False
) -> bool:
    """Share one cancellation read per parent per poll, then recheck before use.

    Model completion always forces a fresh read so caching cannot let a result
    through after the operator cancelled its parent task.
    """
    async with state.cancel_lock:
        if state.cancel_requested:
            return True
        now = asyncio.get_running_loop().time()
        if not refresh and now - state.cancel_checked_at < WAIT_POLL_SECONDS:
            return False
        row = (
            await db.execute(
                select(Task.status, Task.metadata_).where(
                    Task.task_uuid == state.task_uuid,
                )
            )
        ).first()
        state.cancel_requested = bool(
            row
            and (
                row[0] == TaskStatus.CANCELLED or (row[1] or {}).get("cancel_requested")
            )
        )
        state.cancel_checked_at = asyncio.get_running_loop().time()
        return state.cancel_requested


def assert_children_succeeded() -> None:
    state = current_parent()
    if state and FAILURE_THRESHOLDS.get(state.line, 0) is not None and state.failures:
        raise AIWorkFailed(
            f"{state.line}: {len(state.failures)} model work item(s) failed: {state.failures[0]}"
        )


def _write_request(task_uuid: str, content: dict[str, Any]) -> tuple[str, str]:
    # Prompt/document text remains on disk rather than copied into queue JSON.
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    directory = ROOT / "data" / "raw" / "ai" / task_uuid
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as pending:
            pending_path = Path(pending.name)
            try:
                pending.write(encoded)
                pending.flush()
                pending_path.replace(path)
            finally:
                pending_path.unlink(missing_ok=True)
    return str(path.relative_to(ROOT)), digest


def _request(path: str) -> dict[str, Any]:
    resolved = (ROOT / path).resolve()
    resolved.relative_to((ROOT / "data" / "raw" / "ai").resolve())
    return json.loads(resolved.read_text())


async def _lock_reservation(db, resource: str) -> None:
    """Serialize capacity checks, including the empty-lease case.

    Item row locks alone cannot protect a route's remaining permits. The
    transaction lock also coordinates independent dispatcher instances.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        key = int.from_bytes(
            hashlib.sha256(f"ai-work:{resource}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    elif dialect == "sqlite":
        # SQLite has no FOR UPDATE; acquire its write reservation before reading.
        await db.execute(text("BEGIN IMMEDIATE"))


async def submit_completion(
    agent: Any, prompt: str, system: str | None, use_cache: bool, kwargs: dict[str, Any]
) -> str:
    """Enqueue one call and await its durable terminal result, without a slot."""
    state = current_parent()
    if state is None:
        raise RuntimeError(
            "Model work requires a queued parent task; run it through the task worker"
        )
    from .attachments import Attachment

    content = {"prompt": prompt, "system": system, "kwargs": dict(kwargs)}
    attachments = content["kwargs"].pop("attachments", None)
    if attachments:
        import base64

        content["attachments"] = [
            {
                "name": a.name,
                "text": a.text,
                "media_type": a.media_type,
                "data": base64.b64encode(a.data).decode() if a.data else None,
            }
            for a in attachments
            if isinstance(a, Attachment)
        ]
    path, digest = await asyncio.to_thread(_write_request, state.task_uuid, content)
    line = (
        "report_review"
        if state.line == "report_section" and "review" in agent.name.lower()
        else state.line
    )
    payload = {
        "request_path": path,
        "request_key": digest,
        "agent_name": agent.name,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "use_cache": use_cache,
        "avoid_models": list(kwargs.get("_avoid_models") or []),
    }
    # Locking the parent serializes identical submissions across resumed processes.
    async with get_database() as db:
        await _lock_reservation(db, f"parent:{state.task_uuid}")
        await db.execute(
            select(Task.id).where(Task.task_uuid == state.task_uuid).with_for_update()
        )
        candidates = (
            await db.execute(
                select(AIWorkItem)
                .where(
                    AIWorkItem.parent_task_uuid == state.task_uuid,
                    AIWorkItem.payload["request_key"].as_string() == digest,
                    AIWorkItem.status.in_(("queued", "leased", "succeeded")),
                )
                .order_by(AIWorkItem.id.desc())
            )
        ).scalars()
        # Keep the existing request digest compatible with persisted work, while
        # distinguishing calls that use different generation settings.
        item = next(
            (
                candidate
                for candidate in candidates
                if all(
                    candidate.payload.get(key) == payload[key]
                    for key in ("agent_name", "temperature", "max_tokens", "use_cache")
                )
            ),
            None,
        )
        if item is None:
            item = AIWorkItem(
                parent_task_uuid=state.task_uuid,
                line=line,
                priority=state.priority,
                capability="text",
                status="queued",
                payload=payload,
            )
            db.add(item)
            await db.flush()
        work_id = item.id
    state.work_ids.add(work_id)
    async with state.lock:
        state.waiting += 1
        await _set_waiting(state, True)
    try:
        while True:
            async with get_database() as db:
                item = await db.get(AIWorkItem, work_id)
                if await _parent_cancel_requested(
                    db, state, refresh=item.status in TERMINAL_STATUSES
                ):
                    if item.status not in TERMINAL_STATUSES:
                        item.status = "cancelled"
                        item.finished_at = _now()
                    cancelled = True
                else:
                    cancelled = False
                status, result, error = item.status, item.result, item.error
            if cancelled:
                from src.services.exceptions import TaskCancelledError

                raise TaskCancelledError(
                    "Parent task cancelled while awaiting model work"
                )
            if status == "succeeded":
                result = result or {}
                history = result.get("conversation")
                if history:
                    agent.conversation_history.append(history)
                agent._last_input_mode = result.get("input_mode")
                return str(result.get("text") or "")
            if status in {"failed", "cancelled"}:
                message = error or f"work item {work_id} {status}"
                state.failures.append(message)
                raise AIWorkFailed(message)
            await asyncio.sleep(WAIT_POLL_SECONDS)
    finally:
        async with state.lock:
            state.waiting = max(0, state.waiting - 1)
            await _set_waiting(state, state.waiting > 0)


async def submit_probe(operation: str, arguments: dict[str, Any]) -> Any:
    state = current_parent()
    if state is None:
        raise RuntimeError("Health checks require a worker parent operation")
    work_id = await enqueue_work(
        parent_task_uuid=state.task_uuid,
        line="model_probe",
        priority=state.priority,
        capability="health",
        payload={"operation": operation, "arguments": arguments},
    )
    state.work_ids.add(work_id)
    async with state.lock:
        state.waiting += 1
        await _set_waiting(state, True)
    try:
        while True:
            async with get_database() as db:
                item = await db.get(AIWorkItem, work_id)
                status, result, error = item.status, item.result, item.error
            if status == "succeeded":
                return (result or {}).get("operation_result")
            if status in {"failed", "cancelled"}:
                state.failures.append(error or status)
                raise AIWorkFailed(error or status)
            await asyncio.sleep(0.5)
    finally:
        async with state.lock:
            state.waiting = max(0, state.waiting - 1)
            await _set_waiting(state, state.waiting > 0)


async def claim_probe() -> AIWorkItem | None:
    async with get_database() as db:
        await _lock_reservation(db, "route:health:probe")
        # Explicit probes recover disabled/cooling routes and therefore use a
        # separate single health permit, not ordinary routability admission.
        active = (
            await db.execute(
                select(AIWorkItem.id).where(
                    AIWorkItem.capability == "health",
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at > _now(),
                )
            )
        ).first()
        if active:
            return None
        item = (
            await db.execute(
                select(AIWorkItem)
                .where(
                    AIWorkItem.status == "queued",
                    AIWorkItem.capability == "health",
                    AIWorkItem.available_at <= _now(),
                )
                .order_by(AIWorkItem.created_at, AIWorkItem.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if item:
            item.status = "leased"
            item.route_key = "health:probe"
            item.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
            await db.flush()
        return item


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def note_parent_work(event: dict[str, Any]) -> None:
    """Record model-call progress and keep a failure in the parent task log.

    Successful calls only move the parent percentage. A retry or terminal
    failure is written to the workbook, because the work-item row replaces its
    error on the next attempt.
    """

    parent = str(event.get("parent") or "")
    if not parent or parent == "runtime":
        return
    try:
        async with get_database() as db:
            task = (
                await db.execute(select(Task).where(Task.task_uuid == parent))
            ).scalar_one_or_none()
            if task is None or task_type_name(task.task_type) != "agent_workflow":
                return
            rows = (
                await db.execute(
                    select(AIWorkItem.status, func.count())
                    .where(AIWorkItem.parent_task_uuid == parent)
                    .group_by(AIWorkItem.status)
                )
            ).all()
            counts = {str(status): int(count or 0) for status, count in rows}
            progress = displayed_progress(
                task_type=task.task_type,
                status=task.status,
                stored=0,
                counts=counts,
            )
            if task_type_name(task.status) in {"pending", "queued", "running", "retrying"}:
                if int(task.progress or 0) != progress:
                    task.set_progress(progress)
            error = event.get("error")
            if event.get("record_error") and error:
                state = "failed" if event.get("status") == "failed" else "retry"
                db.add(
                    TaskWorkbook(
                        task_id=task.id,
                        entry_type="error" if state == "failed" else "warning",
                        title=f"Model call {state}",
                        content="\n".join(
                            line
                            for line in (
                                f"work_id: {event.get('work_id')}",
                                f"line: {event.get('line')}",
                                f"agent: {event.get('agent')}" if event.get("agent") else "",
                                f"route: {event.get('route')}" if event.get("route") else "",
                                f"attempt: {event.get('attempt')}",
                                f"error: {str(error)[:1000]}",
                            )
                            if line
                        ),
                        content_type="text",
                        model_used=(str(event.get("route"))[:100] if event.get("route") else None),
                        success=False,
                        error_message=str(error)[:1000],
                        metadata_={
                            "source": "ai_work_item",
                            "work_id": event.get("work_id"),
                            "status": event.get("status"),
                        },
                    )
                )
    except Exception:
        logger.exception("Could not record model work progress for {}", parent)


def _work_event(current: AIWorkItem, *, route_key: str | None, record_error: bool) -> dict[str, Any]:
    payload = current.payload if isinstance(current.payload, dict) else {}
    agent = payload.get("agent_name")
    return {
        "parent": current.parent_task_uuid,
        "work_id": current.id,
        "line": current.line,
        "status": current.status,
        "attempt": current.attempt,
        "route": route_key,
        "error": current.error,
        "agent": agent if isinstance(agent, str) and agent.strip() else None,
        "record_error": record_error and bool(current.error),
    }


def _owns_lease(current: AIWorkItem | None, claimed: AIWorkItem) -> bool:
    # Cooldown requeues do not advance attempt. Include the exact lease boundary
    # so a late callback cannot overwrite a subsequent lease on the same route.
    return bool(
        current is not None
        and current.status == "leased"
        and current.attempt == claimed.attempt
        and current.route_key == claimed.route_key
        and current.lease_expires_at is not None
        and claimed.lease_expires_at is not None
        and _utc(current.lease_expires_at) == _utc(claimed.lease_expires_at)
        and _utc(current.lease_expires_at) > _now()
    )


async def enqueue_work(
    *,
    parent_task_uuid: str,
    line: str,
    priority: str,
    payload: dict[str, Any],
    capability: str = "text",
) -> int:
    async with get_database() as db:
        item = AIWorkItem(
            parent_task_uuid=parent_task_uuid,
            line=line,
            priority=priority,
            payload=payload,
            capability=capability,
            status="queued",
        )
        db.add(item)
        await db.flush()
        return item.id


async def recover_expired_leases(*, now: datetime | None = None) -> int:
    now = now or _now()
    async with get_database() as db:
        await _lock_reservation(db, "lease-recovery")
        rows = (
            (
                await db.execute(
                    select(AIWorkItem)
                    .where(
                        AIWorkItem.status == "leased",
                        AIWorkItem.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for item in rows:
            item.lease_expires_at = None
            item.route_key = None
            if item.attempt >= MAX_ATTEMPTS:
                item.status = "failed"
                item.error = "Model work lease expired after three attempts"
                item.finished_at = now
            else:
                item.attempt += 1
                item.status = "queued"
                item.available_at = now
        return len(rows)


async def claim_work(
    route: dict[str, Any],
    *,
    now: datetime | None = None,
    alternatives: set[str] | None = None,
) -> AIWorkItem | None:
    """Atomically reserve a remaining route permit and highest-priority due item."""
    if not route.get("available_for_routing"):
        return None
    now = now or _now()
    key = str(route.get("model_key") or route.get("model_name") or "")
    if not key or int(route.get("runtime_model_capacity") or 0) <= 0:
        return None
    async with get_database() as db:
        await _lock_reservation(db, f"route:{key}")
        leased = await db.scalar(
            select(func.count())
            .select_from(AIWorkItem)
            .where(
                AIWorkItem.status == "leased",
                AIWorkItem.route_key == key,
                AIWorkItem.lease_expires_at > now,
            )
        )
        if leased >= int(route["runtime_model_capacity"]):
            return None
        rank = case(
            (AIWorkItem.priority == "urgent", 0),
            (AIWorkItem.priority == "high", 1),
            (AIWorkItem.priority == "normal", 2),
            else_=3,
        )
        due = (
            AIWorkItem.status == "queued",
            AIWorkItem.available_at <= now,
            AIWorkItem.capability.in_(route.get("capabilities") or ["text"]),
        )
        cursor = None
        while True:
            # Scan only routing metadata. Do not lock or instantiate the entire
            # backlog while looking past items that prefer another route.
            page_query = (
                select(
                    AIWorkItem.id,
                    rank.label("priority_rank"),
                    AIWorkItem.created_at,
                    AIWorkItem.payload["avoid_models"].label("avoid_models"),
                )
                .where(*due)
                .order_by(rank, AIWorkItem.created_at, AIWorkItem.id)
                .limit(CLAIM_SCAN_PAGE_SIZE)
            )
            if cursor is not None:
                page_query = page_query.where(
                    tuple_(rank, AIWorkItem.created_at, AIWorkItem.id) > cursor
                )
            page = (await db.execute(page_query)).all()
            if not page:
                return None
            for candidate in page:
                if not _route_accepts_work(key, candidate.avoid_models, alternatives):
                    continue
                # A different route may have leased this candidate after the
                # metadata scan. Only the selected row is locked, and SKIP
                # LOCKED lets this route continue to the next eligible item.
                item = (
                    await db.execute(
                        select(AIWorkItem)
                        .where(
                            AIWorkItem.id == candidate.id,
                            *due,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if item is None or not _route_accepts_work(
                    key, item.payload.get("avoid_models"), alternatives
                ):
                    continue
                item.status = "leased"
                item.route_key = key
                item.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                await db.flush()
                return item
            if len(page) < CLAIM_SCAN_PAGE_SIZE:
                return None
            last = page[-1]
            cursor = (last.priority_rank, last.created_at, last.id)


def _route_accepts_work(
    key: str, avoid_models: list[str] | None, alternatives: set[str] | None
) -> bool:
    avoided = set(avoid_models or ())
    if key not in avoided and key.split(":", 1)[-1] not in avoided:
        return True
    # With one route, self-review can proceed. With another healthy route,
    # preserve the work for that route and keep scanning lower-priority items.
    return not any(
        other != key and other not in avoided and other.split(":", 1)[-1] not in avoided
        for other in (alternatives or set())
    )


class AIWorkDispatcher:
    def __init__(self, route_loader=None, complete=None):
        if route_loader is None:
            from .model_center import get_runtime_routes

            route_loader = get_runtime_routes
        self.route_loader = route_loader
        self.complete = complete
        self.active: dict[int, asyncio.Task] = {}
        self.outcomes: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._tick_lock = asyncio.Lock()

    async def _execute(self, item: AIWorkItem, route: dict[str, Any]) -> None:
        token = _DISPATCHING.set(True)
        succeeded = False
        noted: dict[str, Any] | None = None
        try:
            if self.complete:
                result = await self.complete(item, route)
                result = {"text": result} if isinstance(result, str) else result
            else:
                result = await self._complete(item, route)
            async with get_database() as db:
                await _lock_reservation(db, f"work:{item.id}")
                current = await db.get(AIWorkItem, item.id, with_for_update=True)
                if not _owns_lease(current, item):
                    return
                current.result = result
                current.status = "succeeded"
                current.error = None
                current.finished_at = _now()
                current.lease_expires_at = None
                noted = _work_event(
                    current,
                    route_key=str(route.get("model_key") or "") or None,
                    record_error=False,
                )
            succeeded = True
        except asyncio.CancelledError:
            raise  # The persisted lease is recovered by the next dispatcher.
        except Exception as exc:
            from .agents.base import NoModelAvailable
            from .model_center import is_rate_limit_error

            cooling = (
                isinstance(exc, NoModelAvailable)
                or is_rate_limit_error(exc)
                or "429" in str(exc)
            )
            # BaseAgent wraps provider exceptions; re-read persisted route health too.
            try:
                routes = await self.route_loader()
            except Exception:
                logger.exception(
                    "Could not refresh route health after model work failed"
                )
                routes = []
            live = next(
                (r for r in routes if r.get("model_key") == item.route_key), None
            )
            cooling = cooling or bool(live and not live.get("available_for_routing"))
            async with get_database() as db:
                await _lock_reservation(db, f"work:{item.id}")
                current = await db.get(AIWorkItem, item.id, with_for_update=True)
                if not _owns_lease(current, item):
                    return
                current.error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                current.lease_expires_at = None
                current.route_key = None
                if cooling or current.attempt < MAX_ATTEMPTS:
                    current.status = "queued"
                    # Route cooldown belongs to the route; another healthy route
                    # must be able to consume this item immediately.
                    current.available_at = _now()
                    if not cooling:
                        current.attempt += 1
                else:
                    current.status = "failed"
                    current.finished_at = _now()
                noted = _work_event(
                    current,
                    route_key=str(route.get("model_key") or "") or None,
                    record_error=not cooling,
                )
        finally:
            self.outcomes[str(route.get("model_key"))].append(succeeded)
            _DISPATCHING.reset(token)
            if noted is not None and (
                noted["status"] in {"succeeded", "failed"} or noted.get("record_error")
            ):
                await note_parent_work(noted)

    async def _complete(
        self, item: AIWorkItem, route: dict[str, Any]
    ) -> dict[str, Any]:
        from .agents.base import BaseAgent

        class WorkerAgent(BaseAgent):
            async def process(self, **kwargs):
                raise NotImplementedError

        payload = item.payload
        if item.capability == "health":
            from .model_center import (
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
        content = await asyncio.to_thread(_request, payload["request_path"])
        kwargs = dict(content.get("kwargs") or {})
        kwargs.pop("preferred_models", None)
        kwargs.pop("_avoid_models", None)
        if content.get("attachments"):
            import base64

            from .attachments import Attachment

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
            wait_for_model_recovery=False,
            max_attempts_per_model=1,
            max_quota_recovery_rounds=0,
            model_request_timeout_seconds=min(
                float(kwargs.get("model_request_timeout_seconds") or 840), 840
            ),
        )
        agent = WorkerAgent(
            name=payload.get("agent_name") or item.line,
            temperature=payload.get("temperature", 0.1),
            max_tokens=payload.get("max_tokens", 2400),
        )
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
            return await self._tick()

    async def _tick(self) -> int:
        for work_id, task in list(self.active.items()):
            expires = getattr(task, "_ai_lease_expires_at", None)
            if not task.done() and expires is not None and _utc(expires) <= _now():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "Model work dispatcher task failed; lease will be recovered"
                    )
                del self.active[work_id]
        await recover_expired_leases()
        probe = await claim_probe()
        if probe is not None:
            self.active[probe.id] = asyncio.create_task(
                self._execute(probe, {"model_key": "health:probe"}),
                name=f"model-health-{probe.id}",
            )
            self.active[probe.id]._ai_lease_expires_at = probe.lease_expires_at
        routes = [
            r
            for r in await self.route_loader()
            if r.get("available_for_routing") and r.get("runtime_model_capacity", 0) > 0
        ]
        if not routes:
            return 0
        claimed = 0
        # Choose from available permits using the shared recent-success weighting.
        while routes:
            counts = defaultdict(int)
            for task in self.active.values():
                key = getattr(task, "_ai_route_key", "")
                counts[key] += 1
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
            item = await claim_work(
                route, alternatives={r["model_key"] for r in routes}
            )
            if item is None:
                routes.remove(route)
                continue
            task = asyncio.create_task(
                self._execute(item, route), name=f"ai-work-{item.id}"
            )
            task._ai_route_key = route["model_key"]
            task._ai_lease_expires_at = item.lease_expires_at
            self.active[item.id] = task
            claimed += 1
        return claimed

    async def run(self, stop_event: asyncio.Event) -> None:
        last_probe = 0.0
        try:
            while not stop_event.is_set():
                try:
                    now = asyncio.get_running_loop().time()
                    if now - last_probe >= 60:
                        await enqueue_work(
                            parent_task_uuid="runtime",
                            line="model_probe",
                            priority="low",
                            capability="health",
                            payload={
                                "operation": "probe_degraded",
                                "arguments": {"limit": 2},
                            },
                        )
                        last_probe = now
                    await self.tick()
                except Exception:
                    logger.exception("Model work dispatch scan failed")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in self.active.values():
                task.cancel()
            await asyncio.gather(*self.active.values(), return_exceptions=True)
