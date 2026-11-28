"""Unit tests for the read-only `metrics` CLI subcommand."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.core.metrics import OperationalMetricsSnapshot, PrometheusRegistry


def test_metrics_default_outputs_prometheus_text_without_operational_refresh(monkeypatch):
    registry = PrometheusRegistry()
    registry.record_http_request(
        method="GET",
        route="/api/v1/health",
        status_code=200,
        duration_seconds=0.025,
    )
    monkeypatch.setattr("src.core.metrics.metrics_registry", registry)

    async def forbid_refresh(*args, **kwargs):
        pytest.fail("default `metrics` command must not invoke operational refresh")

    monkeypatch.setattr(
        "src.core.operational_metrics.refresh_operational_metrics",
        forbid_refresh,
    )

    result = CliRunner().invoke(app, ["metrics"])

    assert result.exit_code == 0, result.exception
    assert "# HELP gids_process_uptime_seconds Process uptime in seconds." in result.output
    assert "# TYPE gids_process_uptime_seconds gauge" in result.output
    assert "# HELP gids_http_requests_total" in result.output
    assert "# TYPE gids_http_requests_total counter" in result.output
    assert 'gids_http_requests_total{method="GET",route="/api/v1/health",status_class="2xx"} 1' in result.output
    assert "# TYPE gids_http_request_duration_seconds histogram" in result.output
    assert "gids_operational_metrics_scrape_success" not in result.output


def test_metrics_include_operational_forces_refresh_and_renders_snapshot(monkeypatch):
    registry = PrometheusRegistry()
    monkeypatch.setattr("src.core.metrics.metrics_registry", registry)

    calls: list[tuple[PrometheusRegistry, bool]] = []

    async def fake_refresh(target_registry: PrometheusRegistry, *, force: bool = False):
        calls.append((target_registry, force))
        snapshot = OperationalMetricsSnapshot(
            task_queue_tasks={("running", "crawl"): 2, ("failed", "report"): 1},
            publish_dirty_pending={"surveillance": 3},
            publish_dirty_oldest_age_seconds=42.5,
            runtime_components_alive={"api": 1, "worker": 1},
            ai_models_enabled=4,
            scrape_success=True,
            scrape_duration_seconds=0.012,
        )
        target_registry.set_operational_snapshot(snapshot)
        return snapshot

    monkeypatch.setattr(
        "src.core.operational_metrics.refresh_operational_metrics",
        fake_refresh,
    )

    result = CliRunner().invoke(app, ["metrics", "--include-operational"])

    assert result.exit_code == 0, result.exception
    assert calls == [(registry, True)]
    assert 'gids_task_queue_tasks{status="running",task_type="crawl"} 2' in result.output
    assert 'gids_task_queue_tasks{status="failed",task_type="report"} 1' in result.output
    assert 'gids_publish_dirty_pending{scope_type="surveillance"} 3' in result.output
    assert "gids_publish_dirty_oldest_age_seconds 42.5" in result.output
    assert 'gids_runtime_components_alive{role="api"} 1' in result.output
    assert 'gids_runtime_components_alive{role="worker"} 1' in result.output
    assert "gids_ai_models_enabled 4" in result.output
    assert "gids_operational_metrics_scrape_success 1" in result.output
    assert "gids_operational_metrics_scrape_duration_seconds 0.012" in result.output


def test_metrics_does_not_wrap_long_prometheus_lines(monkeypatch):
    registry = PrometheusRegistry()
    long_route = "/api/v1/operations/very-long-route-segment/{resource_id}/diagnostics/status"
    registry.record_http_request(
        method="POST",
        route=long_route,
        status_code=201,
        duration_seconds=0.1,
    )
    monkeypatch.setattr("src.core.metrics.metrics_registry", registry)

    result = CliRunner().invoke(app, ["metrics"], env={"COLUMNS": "40"})

    assert result.exit_code == 0, result.exception
    expected_line = f'gids_http_requests_total{{method="POST",route="{long_route}",status_class="2xx"}} 1'
    assert expected_line in result.output.splitlines()
