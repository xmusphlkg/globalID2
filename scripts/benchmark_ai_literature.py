#!/usr/bin/env python3
"""Measure real backlog workflows in a disposable PostgreSQL schema.

The configured source database is opened read-only. Provider credentials and a
bounded article snapshot are copied to a separate test database, then removed.
Simulated mode exercises real SQL, routing, review transitions and application;
it cannot measure model quality. Live mode spends a bounded number of calls and
never publishes its decisions to the source database.
"""

from __future__ import annotations



import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import event, func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from src.ai.benchmark_snapshot import (
    percentile as percentile,
    source_snapshot as source_snapshot,
)

from src.ai import (  # noqa: E402
    model_center,
    runtime,  # noqa: E402
)
from src.ai.catalogue.routes import build_runtime_routes  # noqa: E402
from src.ai.model_identity import model_identity  # noqa: E402
from src.ai.runtime import payloads, store  # noqa: E402
from src.ai.runtime.dispatcher import AIWorkDispatcher  # noqa: E402
from src.core import database as database_module  # noqa: E402
from src.core.config import get_config  # noqa: E402
from src.domain import (  # noqa: E402
    AIReviewDrain,
    AIReviewWorkflow,
    AIWorkItem,
    Base,
    LiteratureArticle,
    LiteratureStatusEvent,
    LiteratureSummary,
    PublishDirty,
    Task,
    TaskStatus,
    TaskType,
    TaskWorkbook,
)
from src.domain.ai_model_center import (  # noqa: E402
    AIModelCallLog,
    AIModelConfig,
    AIProviderConfig,
)
from src.literature.review_workflow import (
    coordinator as review_coordinator,  # noqa: E402
)
from src.literature.review_workflow import run_literature_review  # noqa: E402
from src.services.ai_content_governance_service import (
    AIContentGovernanceService,  # noqa: E402
)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def benchmark(args):
    from loguru import logger

    # Do not include SDK errors, credential-bearing request objects or article
    # text in the benchmark artifact, even when a provider fails.
    logger.remove()
    random.seed(args.seed)
    config = get_config()
    if not args.test_database_url:
        raise ValueError("TEST_AI_DATABASE_URL is required")
    source_url = config.database.url
    from sqlalchemy.engine import make_url

    source_identity = make_url(source_url)
    target_identity = make_url(args.test_database_url)
    if (
        source_identity.host,
        source_identity.port or 5432,
        source_identity.database,
    ) == (target_identity.host, target_identity.port or 5432, target_identity.database):
        raise ValueError("Benchmark database must be separate from the source")
    providers, models, articles, occupancy, revision = await source_snapshot(args.items)
    if not articles:
        raise ValueError("No eligible unheld literature backlog")
    schema = "ai_bench_" + uuid4().hex
    control = create_async_engine(args.test_database_url, hide_parameters=True)
    engine = create_async_engine(
        args.test_database_url,
        pool_size=10,
        max_overflow=10,
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    temporary = tempfile.TemporaryDirectory(prefix="globalid-ai-bench-")
    old_root = payloads.ROOT
    old_cache = config.ai.enable_cache
    database_module._engine = engine
    database_module._session_maker = async_sessionmaker(engine, expire_on_commit=False)
    payloads.ROOT = Path(temporary.name)
    config.ai.enable_cache = False
    sql = Counter()
    calls, http_attempts, samples, tick_times, background_errors = [], [], [], [], []
    route_context = ContextVar("benchmark_route", default="unknown")
    original_routes = model_center.get_runtime_routes
    original_coordinator_routes = review_coordinator.get_runtime_routes
    dispatch = None
    dispatcher_closed = False
    workers = []
    start = time.perf_counter()
    totals, timeout, budget_reached = None, False, False

    async def close_dispatcher():
        nonlocal dispatcher_closed
        if dispatch is None or dispatcher_closed:
            return
        for active in dispatch.active.values():
            active.cancel()
        await asyncio.gather(*dispatch.active.values(), return_exceptions=True)
        await store.release_owned_leases(dispatch.owner)
        await dispatch.transport.close()
        # The optional branch also supports loading a saved pre-optimization
        # dispatcher in an isolated before/after experiment.
        if getattr(dispatch, "progress", None) is not None:
            try:
                async with asyncio.timeout(0.5):
                    await dispatch.progress.flush(force=True)
            except TimeoutError:
                pass
        dispatcher_closed = True

    try:
        tables = {
            model.__table__
            for model in (
                Task,
                TaskWorkbook,
                AIWorkItem,
                AIReviewDrain,
                AIReviewWorkflow,
                LiteratureArticle,
                LiteratureSummary,
                LiteratureStatusEvent,
                PublishDirty,
                AIProviderConfig,
                AIModelConfig,
                AIModelCallLog,
            )
        }
        while True:
            expanded = tables | {
                fk.column.table for table in tables for fk in table.foreign_keys
            }
            if expanded == tables:
                break
            tables = expanded
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=list(tables))
            )
            await connection.execute(AIProviderConfig.__table__.insert(), providers)
            await connection.execute(AIModelConfig.__table__.insert(), models)
            await connection.execute(LiteratureArticle.__table__.insert(), articles)
        sessions = database_module._session_maker
        task = Task(
            task_uuid=str(uuid4()),
            task_name="Isolated literature throughput benchmark",
            task_type=TaskType.AGENT_WORKFLOW,
            status=TaskStatus.RUNNING,
            priority="low",
            input_data={"operation": "governance_stream"},
            metadata_={},
        )
        async with sessions.begin() as db:
            db.add(task)
        async with sessions() as db:
            snapshot = (
                await db.scalars(
                    select(AIModelConfig)
                    .options(selectinload(AIModelConfig.provider))
                    .where(AIModelConfig.is_enabled.is_(True))
                )
            ).all()
        routes = [
            route
            for route in build_runtime_routes(snapshot)
            if route["available_for_routing"]
            and next(p for p in providers if p["id"] == route["provider_id"])[
                "is_active"
            ]
        ]
        if args.mode == "simulated" and args.capacity:
            routes = [
                dict(
                    model_key=f"simulation:model-{i}",
                    model_name=f"model-{i}",
                    available_for_routing=True,
                    runtime_model_capacity=args.capacity // 4 + (i < args.capacity % 4),
                )
                for i in range(4)
            ]
            routes = [route for route in routes if route["runtime_model_capacity"]]
        if args.mode == "live":
            # Existing production executors still consume the same provider
            # accounts. Keep headroom and cap this validation on every route.
            routes = [
                dict(
                    route,
                    runtime_model_capacity=min(
                        args.live_per_route,
                        max(
                            0,
                            route["runtime_model_capacity"]
                            - occupancy.get(route["model_key"], 0)
                            - 1,
                        ),
                    ),
                )
                for route in routes
            ]
            routes = [route for route in routes if route["runtime_model_capacity"] > 0]
        capacity = sum(route["runtime_model_capacity"] for route in routes)
        if not capacity:
            raise ValueError("No routable capacity with test headroom")
        route_limits = {
            route["model_key"]: route["runtime_model_capacity"] for route in routes
        }
        report_routes = [
            {
                "model_key": r["model_key"],
                "capacity": r["runtime_model_capacity"],
                "source_leased": occupancy.get(r["model_key"], 0),
            }
            for r in routes
        ]

        async def route_loader():
            if args.mode == "simulated":
                return routes
            async with sessions() as db:
                current = (
                    await db.scalars(
                        select(AIModelConfig)
                        .options(selectinload(AIModelConfig.provider))
                        .where(AIModelConfig.model_key.in_(route_limits))
                    )
                ).all()
            return [
                dict(route, runtime_model_capacity=route_limits[route["model_key"]])
                for route in build_runtime_routes(current)
            ]

        model_center.get_runtime_routes = route_loader
        review_coordinator.get_runtime_routes = route_loader

        dispatch = AIWorkDispatcher(route_loader=route_loader)
        original_complete = dispatch._complete
        original_client = dispatch.transport._client
        instrumented_clients = set()

        def measured_client(route, style):
            client = original_client(route, style)
            http_client = client._client
            if id(http_client) in instrumented_clients:
                return client
            instrumented_clients.add(id(http_client))
            original_send = http_client.send

            async def measured_send(request, *send_args, **send_kwargs):
                nonlocal budget_reached
                if len(http_attempts) >= args.live_max_calls:
                    budget_reached = True
                    raise RuntimeError("Benchmark HTTP request budget exhausted")
                record = {
                    "route": route_context.get(),
                    "start": time.perf_counter() - start,
                    "end": None,
                    "status": "running",
                }
                http_attempts.append(record)
                try:
                    response = await original_send(request, *send_args, **send_kwargs)
                    record["status_code"] = response.status_code
                    record["status"] = (
                        "succeeded" if response.status_code < 400 else "failed"
                    )
                    return response
                except asyncio.CancelledError:
                    record["status"] = "cancelled"
                    raise
                except Exception as exc:
                    record.update(
                        status="failed",
                        error_type=type(exc).__name__,
                        status_code=getattr(exc, "status_code", None),
                    )
                    raise
                finally:
                    record["end"] = time.perf_counter() - start

            http_client.send = measured_send
            return client

        if args.mode == "live":
            dispatch.transport._client = measured_client

        async def measured_complete(item, route):
            nonlocal budget_reached
            if args.mode == "live" and len(http_attempts) >= args.live_max_calls:
                budget_reached = True
                raise RuntimeError("Benchmark live call budget exhausted")
            record = {
                "id": item.id,
                "route": route["model_key"],
                "step": item.payload.get("workflow_step"),
                "start": time.perf_counter() - start,
                "end": None,
                "status": "running",
                "activity_age_seconds": (
                    datetime.now(timezone.utc) - item.created_at
                ).total_seconds(),
                "ready_queue_wait_seconds": max(
                    0, (item.leased_at - item.available_at).total_seconds()
                ),
            }
            calls.append(record)
            route_token = route_context.set(route["model_key"])
            try:
                if args.mode == "live":
                    async with asyncio.timeout(args.call_timeout):
                        result = await original_complete(item, route)
                    record["tokens"] = (result.get("conversation") or {}).get(
                        "tokens", {}
                    )
                else:
                    await asyncio.sleep(args.latency)
                    if item.payload.get("workflow_step") == "author":
                        # This deterministic reply validates the transaction path;
                        # it is never evidence of model judgment or quality.
                        request = payloads.read_request(item.payload["request_path"])
                        prompt = json.loads(request["prompt"])
                        excerpt = prompt["item"]["abstract"][:180]
                        response = json.dumps(
                            {
                                "decision": "publish",
                                "reasons": [f'Benchmark evidence "{excerpt}"'],
                            }
                        )
                    else:
                        response = '{"verdict":"accept"}'
                    result = {
                        "text": response,
                        "conversation": {
                            "metadata": {
                                "runtime_route": {"model_key": route["model_key"]}
                            }
                        },
                    }
                record["status"] = "succeeded"
                return result
            except asyncio.CancelledError:
                record["status"] = "cancelled"
                raise
            except Exception as exc:
                record["status"], record["error_type"] = "failed", type(exc).__name__
                if isinstance(exc, TypeError):
                    record["error_detail"] = str(exc)[:240]
                raise
            finally:
                record["end"] = time.perf_counter() - start
                route_context.reset(route_token)

        dispatch.complete = measured_complete

        @event.listens_for(engine.sync_engine, "after_cursor_execute")
        def count_sql(_conn, _cursor, statement, _params, _context, _many):
            sql[statement.split(None, 1)[0].upper()] += 1

        stop = asyncio.Event()

        async def dispatch_loop():
            while not stop.is_set():
                dispatch._wake.clear()
                before = time.perf_counter()
                await dispatch.tick()
                tick_times.append(time.perf_counter() - before)
                await dispatch._idle(stop)

        async def monitor():
            while not stop.is_set():
                async with sessions() as db:
                    counts = dict(
                        (
                            await db.execute(
                                select(AIWorkItem.status, func.count()).group_by(
                                    AIWorkItem.status
                                )
                            )
                        ).all()
                    )
                    completed = await db.scalar(
                        select(func.count())
                        .select_from(AIReviewWorkflow)
                        .where(AIReviewWorkflow.status == "terminal")
                    )
                    valid_leases = await db.scalar(
                        select(func.count())
                        .select_from(AIWorkItem)
                        .where(
                            AIWorkItem.status == "leased",
                            AIWorkItem.capability != "health",
                            AIWorkItem.lease_expires_at > datetime.now(timezone.utc),
                        )
                    )
                samples.append(
                    {
                        "t": round(time.perf_counter() - start, 3),
                        **counts,
                        "inflight": sum(call["end"] is None for call in calls),
                        "valid_text_leases": valid_leases,
                        "http_inflight": sum(
                            call["end"] is None for call in http_attempts
                        ),
                        "completed": completed,
                    }
                )
                if budget_reached:
                    break
                await asyncio.sleep(0.25)

        emit(
            {
                "stage": "start",
                "mode": args.mode,
                "items": len(articles),
                "capacity": capacity,
                "buffer": args.buffer_size,
                "source_revision": revision,
                "routes": report_routes,
            }
        )
        start = time.perf_counter()

        async def review():
            async with runtime.task_context(task):
                return await run_literature_review(
                    kind="articles",
                    buffer_size=args.buffer_size,
                    max_items=len(articles),
                    deadline_seconds=max(1, int(args.duration)),
                )

        review_task = asyncio.create_task(review())
        workers = [
            asyncio.create_task(dispatch_loop()),
            asyncio.create_task(monitor()),
            review_task,
        ]
        try:
            done, _pending = await asyncio.wait(
                workers, timeout=args.duration, return_when=asyncio.FIRST_COMPLETED
            )
            timeout = not done
            for finished in done:
                if finished.exception() is not None:
                    background_errors.append(type(finished.exception()).__name__)
                elif finished is review_task:
                    totals = finished.result()
        finally:
            stop.set()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await close_dispatcher()
        elapsed = time.perf_counter() - start
        async with sessions() as db:
            workflow_outcomes = dict(
                (
                    await db.execute(
                        select(AIReviewWorkflow.outcome, func.count()).group_by(
                            AIReviewWorkflow.outcome
                        )
                    )
                ).all()
            )
            statuses = dict(
                (
                    await db.execute(
                        select(AIWorkItem.status, func.count()).group_by(
                            AIWorkItem.status
                        )
                    )
                ).all()
            )
        # Time-weighted occupancy includes warmup and finite-batch drain. The
        # interior window is reported separately and never called sustainable QPS.
        events = sorted(
            [(call["start"], 1) for call in calls]
            + [(call["end"], -1) for call in calls if call["end"] is not None]
        )
        inflight, peak, first_full = 0, 0, None
        for stamp, delta in events:
            inflight += delta
            peak = max(peak, inflight)
            if inflight >= capacity and first_full is None:
                first_full = stamp
        busy_seconds = sum((call["end"] or elapsed) - call["start"] for call in calls)
        interior = [s for s in samples if 0.1 * elapsed <= s["t"] <= 0.8 * elapsed]
        completed_count = sum(
            n for outcome, n in workflow_outcomes.items() if outcome is not None
        )
        report = {
            "at": datetime.now(timezone.utc).isoformat(),
            "mode": args.mode,
            "seed": args.seed,
            "input_identity_sha256": hashlib.sha256(
                json.dumps(
                    [
                        (row["article_id"], row["title"], row["abstract_text"])
                        for row in articles
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "source_revision": revision,
            "source_rows_copied": len(articles),
            "buffer_size": args.buffer_size,
            "capacity": capacity,
            "routes": report_routes,
            "simulated_latency_seconds": args.latency
            if args.mode == "simulated"
            else None,
            "elapsed_seconds": round(elapsed, 3),
            "timed_out": timeout,
            "call_budget_reached": budget_reached,
            "activity_attempts": len(calls),
            "activity_attempt_outcomes": dict(Counter(c["status"] for c in calls)),
            "peak_active_executions": peak,
            "background_errors": background_errors,
            "supply_ceiling": min(len(articles), args.buffer_size, capacity),
            "distinct_model_count": len(
                {model_identity(r["model_key"]) for r in routes}
            ),
            "seconds_to_full_capacity": round(first_full, 3)
            if first_full is not None
            else None,
            "whole_run_execution_occupancy": round(
                busy_seconds / (elapsed * capacity), 4
            ),
            "interior_sampled_occupancy": round(
                sum(s["inflight"] for s in interior) / (len(interior) * capacity), 4
            )
            if interior
            else None,
            "terminal_workflows": completed_count,
            "terminal_workflows_per_minute": round(completed_count * 60 / elapsed, 2),
            "reviewed_workflows": sum(
                workflow_outcomes.get(k, 0) for k in ("published", "rejected", "held")
            ),
            "workflow_outcomes": {str(k): v for k, v in workflow_outcomes.items()},
            "activity_statuses": statuses,
            "execution_latency_p50_seconds": percentile(
                [c["end"] - c["start"] for c in calls if c["end"]], 0.5
            ),
            "execution_latency_p95_seconds": percentile(
                [c["end"] - c["start"] for c in calls if c["end"]], 0.95
            ),
            "ready_queue_wait_p50_seconds": percentile(
                [c["ready_queue_wait_seconds"] for c in calls], 0.5
            ),
            "ready_queue_wait_p95_seconds": percentile(
                [c["ready_queue_wait_seconds"] for c in calls], 0.95
            ),
            "tick_p95_seconds": percentile(tick_times, 0.95),
            "sql_statements": dict(sql),
            "totals": totals,
            "samples": samples,
            "attempts": calls,
            "http_requests": len(http_attempts),
            "http_outcomes": dict(Counter(c["status"] for c in http_attempts)),
            "http_latency_p50_seconds": percentile(
                [c["end"] - c["start"] for c in http_attempts if c["end"]], 0.5
            ),
            "http_latency_p95_seconds": percentile(
                [c["end"] - c["start"] for c in http_attempts if c["end"]], 0.95
            ),
            "http_attempts": http_attempts,
            "limits": [
                "Decisions and health updates remain in a disposable database.",
                "Occupancy includes warmup and finite-batch drain; this is not sustained provider throughput.",
                "Simulated replies do not measure model quality; live calls use a per-route cap and total call budget.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        emit(
            {
                key: value
                for key, value in report.items()
                if key
                not in {"samples", "attempts", "http_attempts", "routes", "limits"}
            }
        )
        return report
    finally:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await close_dispatcher()
        await database_module.dispose_database()
        payloads.ROOT = old_root
        model_center.get_runtime_routes = original_routes
        review_coordinator.get_runtime_routes = original_coordinator_routes
        config.ai.enable_cache = old_cache
        temporary.cleanup()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-database-url", default=os.environ.get("TEST_AI_DATABASE_URL")
    )
    parser.add_argument("--mode", choices=["simulated", "live"], default="simulated")
    parser.add_argument("--items", type=int, default=512)
    parser.add_argument("--buffer-size", type=int, default=512)
    parser.add_argument(
        "--capacity",
        type=int,
        help="Simulated total capacity across four distinct models; defaults to source limits",
    )
    parser.add_argument("--latency", type=float, default=2)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--live-per-route", type=int, default=2)
    parser.add_argument("--live-max-calls", type=int, default=64)
    parser.add_argument("--call-timeout", type=float, default=90)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.items <= 4096 or not 1 <= args.buffer_size <= 512:
        parser.error("items must be 1..4096 and buffer-size 1..512")
    if args.mode == "live" and args.capacity:
        parser.error("capacity override is only for simulation")
    if args.capacity is not None and args.capacity < 1:
        parser.error("capacity must be positive")
    if (
        args.duration <= 0
        or args.latency < 0
        or args.live_per_route < 1
        or args.live_max_calls < 1
        or args.call_timeout <= 0
    ):
        parser.error("Timing and live limits must be positive")
    report = asyncio.run(benchmark(args))
    return 1 if report["background_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
