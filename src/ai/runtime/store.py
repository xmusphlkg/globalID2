"""Transactional activities and globally bounded, owner-fenced route leases."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, text, tuple_

from src.ai.model_identity import model_matches
from src.core import get_database
from src.domain import Task, TaskStatus
from src.domain.ai_work_item import AIWorkItem

from .contracts import (
    CLAIM_SCAN_PAGE_SIZE,
    EXECUTION_DEADLINE_SECONDS,
    HEALTH_PROBE_PERMITS,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    QUEUE_WAIT_SECONDS,
    RUNTIME_PARENT,
)
from .signals import publish_changes

CLAIM_BATCH_SIZE = 16


async def _lock_reservation(db, resource: str) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        key = int.from_bytes(
            hashlib.sha256(f"ai-work:{resource}".encode()).digest()[:8],
            "big",
            signed=True,
        )
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    elif dialect == "sqlite" and not db.info.get("ai_write_reserved"):
        # SQLite is used by unit tests; PostgreSQL remains the production arbiter.
        await db.execute(text("BEGIN IMMEDIATE"))
        db.info["ai_write_reserved"] = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _priority_rank():
    return case(
        (AIWorkItem.priority == "urgent", 0),
        (AIWorkItem.priority == "high", 1),
        (AIWorkItem.priority == "normal", 2),
        else_=3,
    )


def _owns_lease(current: AIWorkItem | None, claimed: AIWorkItem) -> bool:
    return bool(
        current is not None
        and current.status == "leased"
        and current.lease_owner == claimed.lease_owner
        and current.lease_token
        and current.lease_token == claimed.lease_token
        and current.lease_expires_at
        and _utc(current.lease_expires_at) > _now()
        and current.deadline_at
        and _utc(current.deadline_at) > _now()
    )


def clear_lease(item: AIWorkItem) -> None:
    item.lease_owner = item.lease_token = None
    item.lease_expires_at = item.leased_at = item.deadline_at = None


def _lease(item: AIWorkItem, owner: str, route_key: str, now: datetime) -> None:
    if not owner:
        raise ValueError("A lease requires a dispatcher owner")
    item.status = "leased"
    item.route_key = route_key
    item.lease_owner = owner
    item.lease_token = str(uuid4())
    item.leased_at = now
    item.deadline_at = now + timedelta(seconds=EXECUTION_DEADLINE_SECONDS)
    item.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)


async def submit_activity(
    db,
    *,
    parent_task_uuid: str,
    line: str,
    priority: str,
    payload: dict[str, Any],
    capability: str = "text",
) -> AIWorkItem:
    """Submit one logical activity in the caller's parent/workflow transaction."""
    return (
        await submit_activities(
            db,
            parent_task_uuid=parent_task_uuid,
            line=line,
            priority=priority,
            payloads=[payload],
            capability=capability,
        )
    )[0]


async def submit_activities(
    db,
    *,
    parent_task_uuid: str,
    line: str,
    priority: str,
    payloads: list[dict[str, Any]],
    capability: str = "text",
) -> list[AIWorkItem]:
    """Submit a batch with one key lookup, one flush and combined notifications.

    The caller owns the transaction and parent/workflow lock. Request keys
    identify a logical activity including its generation and routing inputs;
    terminal activities replay unchanged. Duplicate keys within this parent and
    batch return the same object, in input order. Unkeyed entries remain distinct.
    """
    if not payloads:
        return []
    keys = {
        payload["request_key"] for payload in payloads if payload.get("request_key")
    }
    by_key: dict[str, AIWorkItem] = {}
    if keys:
        existing = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.parent_task_uuid == parent_task_uuid,
                    AIWorkItem.payload["request_key"].as_string().in_(keys),
                )
                .order_by(AIWorkItem.id.desc())
            )
        ).all()
        for item in existing:
            by_key.setdefault(item.payload["request_key"], item)
    result: list[AIWorkItem] = []
    new_items: list[AIWorkItem] = []
    for payload in payloads:
        key = payload.get("request_key")
        item = by_key.get(key) if key else None
        if item is None:
            item = AIWorkItem(
                parent_task_uuid=parent_task_uuid,
                line=line,
                priority=priority,
                payload=payload,
                capability=capability,
                status="queued",
            )
            new_items.append(item)
            if key:
                by_key[key] = item
        result.append(item)
    if new_items:
        db.add_all(new_items)
        await db.flush()
        await publish_changes(db, work_ids=[item.id for item in new_items])
    return result


async def enqueue_work(**kwargs) -> int:
    async with get_database() as db:
        await _lock_reservation(db, f"parent:{kwargs['parent_task_uuid']}")
        return (await submit_activity(db, **kwargs)).id


async def enqueue_runtime_probe() -> int | None:
    async with get_database() as db:
        await _lock_reservation(db, "runtime-probe")
        pending = await db.scalar(
            select(AIWorkItem.id)
            .where(
                AIWorkItem.parent_task_uuid == RUNTIME_PARENT,
                AIWorkItem.capability == "health",
                AIWorkItem.status.in_(("queued", "leased")),
            )
            .limit(1)
        )
        if pending is not None:
            return None
        return (
            await submit_activity(
                db,
                parent_task_uuid=RUNTIME_PARENT,
                line="model_probe",
                priority="low",
                payload={"operation": "probe_degraded", "arguments": {"limit": 2}},
                capability="health",
            )
        ).id


async def claim_probe(*, owner: str) -> AIWorkItem | None:
    now = _now()
    async with get_database() as db:
        await _lock_reservation(db, "route:health:probe")
        leased = await db.scalar(
            select(func.count())
            .select_from(AIWorkItem)
            .where(
                AIWorkItem.capability == "health",
                AIWorkItem.status == "leased",
                AIWorkItem.lease_expires_at > now,
            )
        )
        if leased >= HEALTH_PROBE_PERMITS:
            return None
        item = await db.scalar(
            select(AIWorkItem)
            .where(
                AIWorkItem.status == "queued",
                AIWorkItem.capability == "health",
                AIWorkItem.created_at > now - timedelta(seconds=QUEUE_WAIT_SECONDS),
                AIWorkItem.available_at <= now,
            )
            .order_by(_priority_rank(), AIWorkItem.created_at, AIWorkItem.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is not None:
            _lease(item, owner, "health:probe", now)
            await db.flush()
        return item


async def renew_owned_leases(owner: str, *, now: datetime | None = None) -> set[int]:
    now = now or _now()
    async with get_database() as db:
        rows = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.lease_owner == owner,
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at > now,
                    AIWorkItem.deadline_at > now,
                )
                .with_for_update()
            )
        ).all()
        for item in rows:
            item.lease_expires_at = min(
                now + timedelta(seconds=LEASE_SECONDS), _utc(item.deadline_at)
            )
        return {item.id for item in rows}


async def release_owned_leases(owner: str, *, now: datetime | None = None) -> int:
    """Graceful shutdown releases only work belonging to this dispatcher."""
    now = now or _now()
    async with get_database() as db:
        rows = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_owner == owner,
                )
                .with_for_update()
            )
        ).all()
        for item in rows:
            clear_lease(item)
            item.route_key = None
            if item.cancel_requested:
                item.status, item.finished_at = "cancelled", now
            else:
                item.status, item.available_at = "queued", now
        await publish_changes(db, work_ids=[item.id for item in rows])
        return len(rows)


async def recover_expired_leases(*, now: datetime | None = None) -> int:
    now = now or _now()
    async with get_database() as db:
        await _lock_reservation(db, "lease-recovery")
        rows = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for item in rows:
            clear_lease(item)
            item.route_key = None
            if item.cancel_requested:
                item.status, item.finished_at = "cancelled", now
            elif item.attempt >= MAX_ATTEMPTS:
                item.status, item.finished_at = "failed", now
                item.error = "Model activity lease expired after maximum attempts"
            else:
                item.attempt += 1
                item.status, item.available_at = "queued", now
        await publish_changes(db, work_ids=[item.id for item in rows])
        return len(rows)


def _capable_alternatives(alternatives, capability: str) -> set[str]:
    if isinstance(alternatives, Mapping):
        return {
            key
            for key, capabilities in alternatives.items()
            if capability in capabilities
        }
    return set(alternatives or ())


def _route_accepts_work(
    key: str,
    avoid_models,
    alternatives: set[str] | None,
    last_failed_route: str | None = None,
    *,
    require_distinct_model: bool = False,
    preferred_models=(),
) -> bool:
    avoided = set(avoid_models or ())
    eligible = {
        other
        for other in (alternatives or ())
        if other != key and not model_matches(other, avoided)
    }
    if model_matches(key, avoided):
        return not require_distinct_model and not eligible
    if key == last_failed_route and eligible:
        return False
    preferred = set(preferred_models or ())
    if (
        preferred
        and not model_matches(key, preferred)
        and any(model_matches(other, preferred) for other in eligible)
    ):
        return False
    return True


async def claim_work(
    route: dict[str, Any],
    *,
    owner: str,
    now: datetime | None = None,
    alternatives: set[str] | Mapping[str, list[str]] | None = None,
) -> AIWorkItem | None:
    """Claim one activity using the same reservation transaction as batch admission."""
    items = await claim_work_batch(
        route, owner=owner, limit=1, now=now, alternatives=alternatives
    )
    return items[0] if items else None


async def claim_work_batch(
    route: dict[str, Any],
    *,
    owner: str,
    limit: int = 16,
    now: datetime | None = None,
    alternatives: set[str] | Mapping[str, list[str]] | None = None,
) -> list[AIWorkItem]:
    """Reserve a bounded batch under one route lock and one occupancy read.

    Candidate pages contain lightweight routing metadata. Only eligible rows are
    locked and materialized; SKIP LOCKED permits other routes to claim work at
    the same time. The route lock still serializes its global permit accounting.
    """
    if not route.get("available_for_routing") or limit <= 0:
        return []
    now = now or _now()
    key = str(route.get("model_key") or "")
    capacity = int(route.get("runtime_model_capacity") or 0)
    if not key or capacity <= 0:
        return []
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
        slots = min(capacity - leased, int(limit), CLAIM_BATCH_SIZE)
        if slots <= 0:
            return []
        rank = _priority_rank()
        due = (
            AIWorkItem.status == "queued",
            AIWorkItem.available_at <= now,
            AIWorkItem.created_at > now - timedelta(seconds=QUEUE_WAIT_SECONDS),
            AIWorkItem.capability.in_(route.get("capabilities") or ["text"]),
        )

        def eligible(payload, capability):
            return _route_accepts_work(
                key,
                payload.get("avoid_models"),
                _capable_alternatives(alternatives, capability),
                payload.get("last_failed_route"),
                require_distinct_model=bool(payload.get("require_distinct_model")),
                preferred_models=payload.get("preferred_models"),
            )

        claimed: list[AIWorkItem] = []
        cursor = None
        while len(claimed) < slots:
            query = (
                select(
                    AIWorkItem.id,
                    rank.label("priority_rank"),
                    AIWorkItem.created_at,
                    AIWorkItem.capability,
                    AIWorkItem.payload,
                )
                .where(*due)
                .order_by(rank, AIWorkItem.created_at, AIWorkItem.id)
                .limit(CLAIM_SCAN_PAGE_SIZE)
            )
            if cursor is not None:
                query = query.where(
                    tuple_(rank, AIWorkItem.created_at, AIWorkItem.id) > cursor
                )
            page = (await db.execute(query)).all()
            if not page:
                break
            candidates = [
                candidate.id
                for candidate in page
                if eligible(candidate.payload or {}, candidate.capability)
            ]
            while candidates and len(claimed) < slots:
                remaining = slots - len(claimed)
                selected, candidates = candidates[:remaining], candidates[remaining:]
                rows = (
                    await db.scalars(
                        select(AIWorkItem)
                        .where(AIWorkItem.id.in_(selected), *due)
                        .order_by(rank, AIWorkItem.created_at, AIWorkItem.id)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                for item in rows:
                    if eligible(item.payload or {}, item.capability):
                        _lease(item, owner, key, now)
                        claimed.append(item)
            if len(page) < CLAIM_SCAN_PAGE_SIZE:
                break
            last = page[-1]
            cursor = (last.priority_rank, last.created_at, last.id)
        if claimed:
            await db.flush()
        return claimed


async def active_route_lease_counts(*, now: datetime | None = None) -> dict[str, int]:
    async with get_database() as db:
        rows = (
            await db.execute(
                select(AIWorkItem.route_key, func.count())
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_expires_at > (now or _now()),
                    AIWorkItem.route_key.is_not(None),
                )
                .group_by(AIWorkItem.route_key)
            )
        ).all()
    return {str(key): int(count) for key, count in rows}


async def expire_queued_activities(*, now: datetime | None = None) -> int:
    """End activities still queued six hours after submission, including retries.

    Leased executions keep their own deadline. A strict routing requirement may
    remain unsatisfied, but its business parent still receives a terminal result.
    """
    now = now or _now()
    cutoff = now - timedelta(seconds=QUEUE_WAIT_SECONDS)
    async with get_database() as db:
        await _lock_reservation(db, "queue-expiry")
        rows = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.status == "queued",
                    AIWorkItem.created_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for item in rows:
            item.status = "failed"
            item.finished_at = now
            item.error = f"Model activity exceeded maximum queue age of {QUEUE_WAIT_SECONDS} seconds"
            clear_lease(item)
        await publish_changes(db, work_ids=[item.id for item in rows])
        return len(rows)


async def request_cancellation(db, item: AIWorkItem) -> None:
    """Request cancellation without returning a live HTTP request's permit.

    The caller owns this transaction. Refresh under the row lock because a
    queued item may have been leased since the caller first loaded it.
    """
    current = await lock_activity(db, item.id)
    if current is None or current.status not in {"queued", "leased"}:
        return
    current.cancel_requested = True
    if current.status == "queued":
        current.status, current.finished_at = "cancelled", _now()
        clear_lease(current)
    await publish_changes(db, work_ids=[current.id])


async def restart_parent_activities(db, task: Task) -> int:
    """Begin an explicit new execution; caller holds the parent row lock.

    Terminal history remains immutable. Active HTTP retains its permit until
    its dispatcher finishes cancellation, even after the parent is requeued.
    """
    from .context import parent_execution_generation

    generation = parent_execution_generation(task) + 1
    active = (
        await db.scalars(
            select(AIWorkItem)
            .where(
                AIWorkItem.parent_task_uuid == task.task_uuid,
                AIWorkItem.status.in_(("queued", "leased")),
            )
            .order_by(AIWorkItem.id)
        )
    ).all()
    for item in active:
        await request_cancellation(db, item)
    task.metadata_ = {**(task.metadata_ or {}), "ai_execution_generation": generation}
    await publish_changes(db, parent=task.task_uuid)
    return generation


async def claim_owned_cancellations(owner: str) -> list[AIWorkItem]:
    """Find this executor's pending cancellations, including cancelled parents."""
    changed_parent = (
        select(Task.id)
        .where(
            Task.task_uuid == AIWorkItem.parent_task_uuid,
            or_(
                Task.status.in_(
                    (TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.COMPLETED)
                ),
                Task.metadata_["cancel_requested"].as_boolean().is_(True),
                func.coalesce(Task.metadata_["ai_execution_generation"].as_integer(), 0)
                != func.coalesce(
                    AIWorkItem.payload["execution_generation"].as_integer(), 0
                ),
            ),
        )
        .exists()
    )
    parent_exists = (
        select(Task.id).where(Task.task_uuid == AIWorkItem.parent_task_uuid).exists()
    )
    async with get_database() as db:
        await _lock_reservation(db, f"owner-cancellation:{owner}")
        items = (
            await db.scalars(
                select(AIWorkItem)
                .where(
                    AIWorkItem.status == "leased",
                    AIWorkItem.lease_owner == owner,
                    or_(
                        AIWorkItem.cancel_requested.is_(True),
                        changed_parent,
                        and_(
                            AIWorkItem.parent_task_uuid != RUNTIME_PARENT,
                            ~parent_exists,
                        ),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for item in items:
            item.cancel_requested = True
        return list(items)


async def finish_cancelled_lease(claimed: AIWorkItem) -> bool:
    """Release a cancellation only after the executor has closed provider I/O."""
    async with get_database() as db:
        current = await lock_activity(db, claimed.id)
        if not (
            current is not None
            and current.status == "leased"
            and current.lease_owner == claimed.lease_owner
            and current.lease_token == claimed.lease_token
            and current.cancel_requested
        ):
            return False
        current.status, current.finished_at = "cancelled", _now()
        current.result, current.error = None, None
        clear_lease(current)
        await publish_changes(db, work_ids=[current.id])
        return True


async def lock_activity(db, work_id: int) -> AIWorkItem | None:
    """Refresh one row under its transaction lock, without a second PG lock order."""
    if db.get_bind().dialect.name == "sqlite":
        await _lock_reservation(db, f"work:{work_id}")
    return await db.get(
        AIWorkItem, work_id, populate_existing=True, with_for_update=True
    )
