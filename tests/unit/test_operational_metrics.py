from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dashboard.api import deps
from dashboard.api.main import create_app
from src.control_plane.runtime import runtime_registry
from src.core import database as database_module
from src.core.metrics import (
    PROMETHEUS_CONTENT_TYPE,
    OperationalMetricsSnapshot,
    PrometheusRegistry,
    metrics_registry,
)
from src.core.operational_metrics import (
    OperationalMetricsCollector,
    operational_metrics_collector,
    refresh_operational_metrics,
)
from src.domain.ai_model_center import AIModelConfig, AIProviderConfig
from src.domain.collection import PublishDirty
from src.domain.task import Task, TaskStatus, TaskType

PROMETHEUS_SAMPLE_RE = re.compile(
    r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\]|\\.)*"(,[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\]|\\.)*")*\})? (?:\+Inf|-Inf|NaN|-?\d+(?:\.\d+)?)$'
)


def _assert_valid_prometheus_text(payload: str) -> None:
    seen_help: set[str] = set()
    seen_type: set[str] = set()
    lines = payload.splitlines()
    assert payload.endswith("\n")
    for line in lines:
        if not line:
            continue
        if line.startswith("# HELP "):
            metric_name = line.split(" ", 3)[2]
            assert metric_name not in seen_help, f"Duplicate HELP for {metric_name}"
            seen_help.add(metric_name)
        elif line.startswith("# TYPE "):
            parts = line.split(" ")
            assert len(parts) == 4
            assert parts[3] in {"counter", "gauge", "histogram", "summary", "untyped"}
            metric_name = parts[2]
            assert metric_name not in seen_type, f"Duplicate TYPE for {metric_name}"
            seen_type.add(metric_name)
        else:
            assert PROMETHEUS_SAMPLE_RE.match(line), f"Invalid Prometheus sample line: {line!r}"


@pytest.fixture(autouse=True)
def _reset_state():
    metrics_registry.reset()
    operational_metrics_collector.reset()
    yield
    metrics_registry.reset()
    operational_metrics_collector.reset()


@pytest.mark.asyncio
async def test_collector_queries_orm_models_and_renders_all_operational_metrics() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Task.__table__.create)
        await conn.run_sync(PublishDirty.__table__.create)
        await conn.run_sync(AIProviderConfig.__table__.create)
        await conn.run_sync(AIModelConfig.__table__.create)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now_utc = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        session.add_all(
            [
                Task(
                    task_type=TaskType.CRAWL_DATA,
                    task_name="crawl pending 1",
                    status=TaskStatus.PENDING,
                    created_at=now_utc - timedelta(minutes=10),
                    updated_at=now_utc - timedelta(minutes=10),
                ),
                Task(
                    task_type=TaskType.CRAWL_DATA,
                    task_name="crawl pending 2",
                    status=TaskStatus.PENDING,
                    created_at=now_utc - timedelta(minutes=5),
                    updated_at=now_utc - timedelta(minutes=5),
                ),
                Task(
                    task_type=TaskType.EXPORT_DATA,
                    task_name="export queued",
                    status=TaskStatus.QUEUED,
                    created_at=now_utc - timedelta(minutes=3),
                    updated_at=now_utc - timedelta(minutes=3),
                ),
                Task(
                    task_type=TaskType.GENERATE_REPORT,
                    task_name="report running",
                    status=TaskStatus.RUNNING,
                    created_at=now_utc - timedelta(minutes=2),
                    updated_at=now_utc - timedelta(minutes=1),
                ),
                Task(
                    task_type=TaskType.PROCESS_DATA,
                    task_name="process retrying",
                    status=TaskStatus.RETRYING,
                    created_at=now_utc - timedelta(minutes=15),
                    updated_at=now_utc - timedelta(minutes=1),
                ),
                Task(
                    task_type=TaskType.CRAWL_DATA,
                    task_name="recent failed",
                    status=TaskStatus.FAILED,
                    created_at=now_utc - timedelta(hours=3),
                    updated_at=now_utc - timedelta(hours=2),
                    completed_at=now_utc - timedelta(hours=2),
                ),
                Task(
                    task_type=TaskType.CRAWL_DATA,
                    task_name="old failed (>24h excluded)",
                    status=TaskStatus.FAILED,
                    created_at=now_utc - timedelta(hours=50),
                    updated_at=now_utc - timedelta(hours=48),
                    completed_at=now_utc - timedelta(hours=48),
                ),
                Task(
                    task_type=TaskType.CRAWL_DATA,
                    task_name="completed excluded",
                    status=TaskStatus.COMPLETED,
                    created_at=now_utc - timedelta(minutes=30),
                    updated_at=now_utc - timedelta(minutes=20),
                    completed_at=now_utc - timedelta(minutes=20),
                ),
            ]
        )

        session.add_all(
            [
                PublishDirty(
                    line="surveillance",
                    scope_key="CN",
                    source_task_uuid="t-1",
                    marked_at=now_utc - timedelta(seconds=120),
                    published_at=None,
                ),
                PublishDirty(
                    line="surveillance",
                    scope_key="US",
                    source_task_uuid="t-2",
                    marked_at=now_utc - timedelta(seconds=45),
                    published_at=None,
                ),
                PublishDirty(
                    line="literature",
                    scope_key="global",
                    source_task_uuid="t-3",
                    marked_at=now_utc - timedelta(seconds=30),
                    published_at=None,
                ),
                PublishDirty(
                    line="surveillance",
                    scope_key="JP",
                    source_task_uuid="t-4",
                    marked_at=now_utc - timedelta(seconds=600),
                    published_at=now_utc - timedelta(seconds=500),
                ),
            ]
        )

        active_provider = AIProviderConfig(
            provider_key="openai-prod",
            provider_name="openai",
            display_name="OpenAI Prod",
            is_active=True,
        )
        inactive_provider = AIProviderConfig(
            provider_key="disabled-prov",
            provider_name="custom",
            display_name="Disabled Provider",
            is_active=False,
        )
        session.add_all([active_provider, inactive_provider])
        await session.flush()

        session.add_all(
            [
                AIModelConfig(
                    provider_id=active_provider.id,
                    model_key="openai-prod:gpt-4o",
                    model_name="gpt-4o",
                    display_name="GPT-4o",
                    is_enabled=True,
                ),
                AIModelConfig(
                    provider_id=active_provider.id,
                    model_key="openai-prod:gpt-4o-mini",
                    model_name="gpt-4o-mini",
                    display_name="GPT-4o Mini",
                    is_enabled=True,
                ),
                AIModelConfig(
                    provider_id=active_provider.id,
                    model_key="openai-prod:disabled",
                    model_name="disabled",
                    display_name="Disabled Model",
                    is_enabled=False,
                ),
                AIModelConfig(
                    provider_id=inactive_provider.id,
                    model_key="disabled-prov:model-1",
                    model_name="model-1",
                    display_name="Model on Inactive Provider",
                    is_enabled=True,
                ),
            ]
        )
        await session.commit()

    async def _fake_runtime():
        return (
            [
                {"service": "api", "instance_id": "api-1"},
                {"service": "worker", "instance_id": "worker-1"},
                {"service": "worker", "instance_id": "worker-2"},
                {"service": "ai-worker", "instance_id": "ai-1"},
                {"service": "scheduler", "instance_id": "sched-1"},
            ],
            True,
        )

    collector = OperationalMetricsCollector(
        cache_ttl_seconds=15.0,
        timeout_seconds=1.5,
        session_factory=session_factory,
        runtime_provider=_fake_runtime,
        now_fn=lambda: now_utc,
    )

    reg = PrometheusRegistry()
    snapshot = await collector.refresh(reg)
    assert snapshot is not None
    assert snapshot.scrape_success is True

    rendered = reg.render_prometheus(include_db_pool=False)
    _assert_valid_prometheus_text(rendered)

    assert "# TYPE gids_task_queue_tasks gauge" in rendered
    assert 'gids_task_queue_tasks{status="pending",task_type="crawl_data"} 2' in rendered
    assert 'gids_task_queue_tasks{status="queued",task_type="export_data"} 1' in rendered
    assert 'gids_task_queue_tasks{status="running",task_type="generate_report"} 1' in rendered
    assert 'gids_task_queue_tasks{status="retrying",task_type="process_data"} 1' in rendered
    assert 'gids_task_queue_tasks{status="failed",task_type="crawl_data"} 1' in rendered
    assert 'status="completed"' not in rendered

    assert "# TYPE gids_publish_dirty_pending gauge" in rendered
    assert 'gids_publish_dirty_pending{scope_type="literature"} 1' in rendered
    assert 'gids_publish_dirty_pending{scope_type="surveillance"} 2' in rendered
    assert "# TYPE gids_publish_dirty_oldest_age_seconds gauge" in rendered
    assert "gids_publish_dirty_oldest_age_seconds 120" in rendered

    assert "# TYPE gids_runtime_components_alive gauge" in rendered
    assert 'gids_runtime_components_alive{role="ai-worker"} 1' in rendered
    assert 'gids_runtime_components_alive{role="api"} 1' in rendered
    assert 'gids_runtime_components_alive{role="scheduler"} 1' in rendered
    assert 'gids_runtime_components_alive{role="worker"} 2' in rendered

    assert "# TYPE gids_ai_models_enabled gauge" in rendered
    assert "gids_ai_models_enabled 2" in rendered

    assert "# TYPE gids_operational_metrics_scrape_success gauge" in rendered
    assert "gids_operational_metrics_scrape_success 1" in rendered
    assert "# TYPE gids_operational_metrics_scrape_duration_seconds gauge" in rendered

    # Clearing operational snapshot removes all operational gauges cleanly
    reg.clear_operational_snapshot()
    cleared = reg.render_prometheus(include_db_pool=False)
    assert "gids_publish_dirty_pending" not in cleared
    assert "gids_runtime_components_alive" not in cleared
    assert "gids_ai_models_enabled" not in cleared
    assert "gids_operational_metrics_scrape_success" not in cleared

    await engine.dispose()


@pytest.mark.asyncio
async def test_publish_dirty_empty_reports_zero_oldest_age() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Task.__table__.create)
        await conn.run_sync(PublishDirty.__table__.create)
        await conn.run_sync(AIProviderConfig.__table__.create)
        await conn.run_sync(AIModelConfig.__table__.create)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    collector = OperationalMetricsCollector(
        session_factory=session_factory,
        runtime_provider=lambda: asyncio.sleep(0, result=([], True)),
    )
    reg = PrometheusRegistry()
    await collector.refresh(reg)
    rendered = reg.render_prometheus(include_db_pool=False)
    _assert_valid_prometheus_text(rendered)

    assert "gids_publish_dirty_oldest_age_seconds 0" in rendered
    assert "gids_ai_models_enabled 0" in rendered
    assert "gids_operational_metrics_scrape_success 1" in rendered

    await engine.dispose()


@pytest.mark.asyncio
async def test_ttl_caching_and_concurrent_scrape_coalescing() -> None:
    current_time = [100.0]
    calls = {"task": 0, "dirty": 0, "ai": 0, "runtime": 0}

    async def _task_provider():
        calls["task"] += 1
        await asyncio.sleep(0.02)
        return {("pending", "crawl_data"): calls["task"]}

    async def _dirty_provider():
        calls["dirty"] += 1
        return ({"surveillance": calls["dirty"]}, 15.0)

    async def _ai_provider():
        calls["ai"] += 1
        return 3

    async def _runtime_provider():
        calls["runtime"] += 1
        return ([{"service": "worker", "instance_id": "w-1"}], True)

    collector = OperationalMetricsCollector(
        cache_ttl_seconds=15.0,
        timeout_seconds=1.5,
        task_queue_provider=_task_provider,
        publish_dirty_provider=_dirty_provider,
        ai_models_provider=_ai_provider,
        runtime_provider=_runtime_provider,
        clock=lambda: current_time[0],
    )

    # Concurrent cold scrapes coalesce into a single provider execution
    snap_a, snap_b, snap_c = await asyncio.gather(
        collector.collect(),
        collector.collect(),
        collector.collect(),
    )
    assert calls == {"task": 1, "dirty": 1, "ai": 1, "runtime": 1}
    assert snap_a == snap_b == snap_c
    assert snap_a is not None
    assert snap_a.task_queue_tasks == {("pending", "crawl_data"): 1}

    # Subsequent call within TTL reuses cache without invoking providers
    current_time[0] = 110.0
    snap_cached = await collector.collect()
    assert calls == {"task": 1, "dirty": 1, "ai": 1, "runtime": 1}
    assert snap_cached == snap_a

    # Advancing clock past TTL refreshes snapshot
    current_time[0] = 116.0
    snap_refreshed = await collector.collect()
    assert calls == {"task": 2, "dirty": 2, "ai": 2, "runtime": 2}
    assert snap_refreshed is not None
    assert snap_refreshed.task_queue_tasks == {("pending", "crawl_data"): 2}


@pytest.mark.asyncio
async def test_fail_soft_when_subcollector_raises_exception() -> None:
    async def _failing_ai():
        raise RuntimeError("relation ai_models does not exist")

    async def _ok_tasks():
        return {("running", "generate_report"): 2}

    async def _ok_dirty():
        return ({"knowledge": 4}, 90.0)

    async def _ok_runtime():
        return ([{"service": "api", "instance_id": "api-1"}], True)

    collector = OperationalMetricsCollector(
        cache_ttl_seconds=0.0,
        timeout_seconds=1.0,
        task_queue_provider=_ok_tasks,
        publish_dirty_provider=_ok_dirty,
        ai_models_provider=_failing_ai,
        runtime_provider=_ok_runtime,
    )

    reg = PrometheusRegistry()
    snapshot = await collector.refresh(reg)
    assert snapshot is not None
    assert snapshot.scrape_success is False
    assert snapshot.ai_models_enabled is None

    rendered = reg.render_prometheus(include_db_pool=False)
    _assert_valid_prometheus_text(rendered)
    assert 'gids_task_queue_tasks{status="running",task_type="generate_report"} 2' in rendered
    assert 'gids_publish_dirty_pending{scope_type="knowledge"} 4' in rendered
    assert "gids_publish_dirty_oldest_age_seconds 90" in rendered
    assert 'gids_runtime_components_alive{role="api"} 1' in rendered
    assert "gids_ai_models_enabled" not in rendered
    assert "gids_operational_metrics_scrape_success 0" in rendered


@pytest.mark.asyncio
async def test_fail_soft_when_subcollector_times_out() -> None:
    async def _hanging_tasks():
        await asyncio.sleep(5.0)
        return {("pending", "crawl_data"): 99}

    async def _fast_runtime():
        return ([{"service": "worker", "instance_id": "w-1"}], True)

    collector = OperationalMetricsCollector(
        cache_ttl_seconds=0.0,
        timeout_seconds=0.05,
        task_queue_provider=_hanging_tasks,
        publish_dirty_provider=lambda: asyncio.sleep(0, result=({}, 0.0)),
        ai_models_provider=lambda: asyncio.sleep(0, result=1),
        runtime_provider=_fast_runtime,
    )

    reg = PrometheusRegistry()
    started = asyncio.get_running_loop().time()
    snapshot = await collector.refresh(reg)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5
    assert snapshot is not None
    assert snapshot.scrape_success is False

    rendered = reg.render_prometheus(include_db_pool=False)
    _assert_valid_prometheus_text(rendered)
    assert "gids_operational_metrics_scrape_success 0" in rendered


@pytest.mark.asyncio
async def test_no_engine_fast_path_never_initializes_database_or_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database_module, "_engine", None)
    monkeypatch.setattr(database_module, "_session_maker", None)
    monkeypatch.setattr(runtime_registry, "_redis", None)

    def _fail_get_engine():
        raise AssertionError("get_engine() must not be called when _engine is None")

    def _fail_get_session_maker():
        raise AssertionError("get_session_maker() must not be called when _engine is None")

    async def _fail_list_services():
        raise AssertionError("list_services() must not be called in uninitialized fast path")

    monkeypatch.setattr(database_module, "get_engine", _fail_get_engine)
    monkeypatch.setattr(database_module, "get_session_maker", _fail_get_session_maker)
    monkeypatch.setattr(runtime_registry, "list_services", _fail_list_services)

    collector = OperationalMetricsCollector(cache_ttl_seconds=0.0, timeout_seconds=1.0)
    reg = PrometheusRegistry()
    snapshot = await collector.refresh(reg)

    assert snapshot is not None
    assert snapshot.scrape_success is True
    assert snapshot.task_queue_tasks is None
    assert snapshot.publish_dirty_pending is None
    assert snapshot.ai_models_enabled is None
    assert snapshot.runtime_components_alive is None

    rendered = reg.render_prometheus(include_db_pool=False)
    _assert_valid_prometheus_text(rendered)
    assert "gids_task_queue_tasks" not in rendered
    assert "gids_operational_metrics_scrape_success 1" in rendered


@pytest.mark.asyncio
async def test_operational_metrics_disabled_via_env_skips_and_clears_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METRICS_OPERATIONAL_ENABLED", "0")
    invoked = False

    async def _should_not_run():
        nonlocal invoked
        invoked = True
        return 1

    collector = OperationalMetricsCollector(ai_models_provider=_should_not_run)
    reg = PrometheusRegistry()
    reg.set_operational_snapshot(OperationalMetricsSnapshot(ai_models_enabled=5, scrape_success=True))

    result = await refresh_operational_metrics(reg, collector=collector)
    assert result is None
    assert invoked is False
    assert "gids_ai_models_enabled" not in reg.render_prometheus(include_db_pool=False)


def test_metrics_http_endpoint_returns_200_even_when_operational_scrape_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METRICS_PUBLIC", "1")
    monkeypatch.setattr(
        deps,
        "get_config",
        lambda: SimpleNamespace(dashboard_api_key="", is_production=False),
    )

    failing_collector = OperationalMetricsCollector(
        cache_ttl_seconds=0.0,
        timeout_seconds=0.05,
        task_queue_provider=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        publish_dirty_provider=lambda: asyncio.sleep(5.0),
        ai_models_provider=lambda: asyncio.sleep(0, result=3),
        runtime_provider=lambda: asyncio.sleep(0, result=([], False)),
    )
    monkeypatch.setattr(
        "src.core.operational_metrics.operational_metrics_collector",
        failing_collector,
    )

    app = create_app()
    assert "/metrics" not in app.openapi()["paths"]

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    _assert_valid_prometheus_text(resp.text)
    assert "gids_operational_metrics_scrape_success 0" in resp.text
    assert "gids_process_uptime_seconds" in resp.text
