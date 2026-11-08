from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

from src.services.data_release import subscription_reconciliation as reconcile
from src.services.data_release_service import DataReleaseService


@pytest.mark.asyncio
async def test_independent_reconciliation_retries_without_a_new_release(tmp_path, monkeypatch):
    snapshot = tmp_path / "exports/verified-pages-snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "release.json").write_text(json.dumps({"release_id": "live"}))
    astro = tmp_path / "astro-site"
    (astro / "src").mkdir(parents=True)
    values = {
        "SUBSCRIPTIONS__REPORT_DIGEST_ENABLED": "true", "SUBSCRIPTIONS__REPORT_DIGEST_START_WEEK": "2026-W45",
        "SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED": "true", "SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK": "2026-W45",
        "SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED": "true", "SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE": "2026-11-04",
        "SUBSCRIPTIONS__PUBLIC_BASE_URL": "https://worker.example.test", "SUBSCRIPTIONS__ADMIN_API_TOKEN": "test-only",
        "SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN": "test-only", "GIDS_PUBLIC_BASE_URL": "https://site.example.test",
    }
    service = SimpleNamespace(_env_value=lambda name, default="": values.get(name, default),
        _config=lambda: SimpleNamespace(r2_reports_public_base_url="https://reports.example.test"),
        _reports_site_dir=lambda: tmp_path / "reports")
    monkeypatch.setattr(reconcile, "verified_snapshot", lambda _snapshot, _identity: True)
    calls: list[tuple[str, str]] = []
    attempts = 0
    async def reports(_service, _runtime, *, reports_origin):
        calls.append(("reports", reports_origin))
        return {"status": "completed"}
    def research(**kwargs):
        nonlocal attempts
        attempts += 1
        calls.append(("research", kwargs["start_week"]))
        assert (kwargs["astro_dir"] / "dist/release.json").is_file()
        if attempts == 1:
            raise reconcile.research_subscription.ResearchSubscriptionError("worker_transport_error:TimeoutError")
        return {"status": "completed", "completed_weeks": ["2026-W45"]}
    def notice(**kwargs):
        calls.append(("notices", kwargs["start_date"]))
        return {"status": "no_new_notices"}
    monkeypatch.setattr(reconcile.report_subscription, "dispatch_if_enabled", reports)
    monkeypatch.setattr(reconcile.research_subscription, "dispatch_after_release", research)
    monkeypatch.setattr(reconcile.notice_subscription, "dispatch_after_release", notice)
    output = StringIO()
    sink = logger.add(output, format="{message}")
    try:
        first = await reconcile.reconcile_once(service, root=tmp_path, astro_dir=astro)
        second = await reconcile.reconcile_once(service, root=tmp_path, astro_dir=astro)
    finally:
        logger.remove(sink)
    assert '"event": "subscription_reconciliation"' in output.getvalue()
    assert first["research"]["status"] == "failed"
    assert second["research"]["completed_weeks"] == ["2026-W45"]
    assert calls.count(("research", "2026-W45")) == 2
    assert calls.count(("notices", "2026-11-04")) == 2


@pytest.mark.asyncio
async def test_scheduler_starts_and_stops_when_site_release_is_disabled(monkeypatch):
    service = DataReleaseService()
    monkeypatch.setattr(service, "_config", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(service, "_env_value", lambda name, default="": "true" if name == "SUBSCRIPTIONS__REPORT_DIGEST_ENABLED" else default)
    started = asyncio.Event()
    async def loop(_service, *, root, astro_dir, stop):
        started.set()
        await stop.wait()
    monkeypatch.setattr(reconcile, "run_loop", loop)
    await service.start()
    await asyncio.wait_for(started.wait(), timeout=2)
    first = service._subscription_task
    await service.start()
    assert service._subscription_task is first
    await asyncio.wait_for(service.stop(), timeout=2)
    assert service._subscription_task is None


@pytest.mark.asyncio
async def test_dispatch_failures_do_not_escape_into_the_published_release():
    output = StringIO()
    sink = logger.add(output, format="{message}")
    def fails(**_kwargs):
        raise reconcile.research_subscription.ResearchSubscriptionError("worker_transport_error:TimeoutError")
    try:
        result = await reconcile.dispatch_safely("research", fails, token="test-only-secret")
        assert result == {"status": "failed", "error_code": "worker_transport_error:TimeoutError"}
        assert "subscription_dispatch_failed" in output.getvalue()
        assert "test-only-secret" not in output.getvalue()
        def unexpected(**_kwargs):
            raise ValueError("test-only-secret")
        result = await reconcile.dispatch_safely("notices", unexpected)
        assert result == {"status": "failed", "error_code": "internal_error"}
        assert "test-only-secret" not in output.getvalue()
    finally:
        logger.remove(sink)


@pytest.mark.asyncio
async def test_report_failure_does_not_block_other_subscription_streams(tmp_path, monkeypatch):
    snapshot = tmp_path / "exports/verified-pages-snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "release.json").write_text(json.dumps({"release_id": "live"}))
    astro = tmp_path / "astro-site"
    (astro / "src").mkdir(parents=True)
    service = SimpleNamespace(
        _env_value=lambda name, default="": "true" if name.endswith("_ENABLED") else default,
        _config=lambda: SimpleNamespace(r2_reports_public_base_url="https://reports.example.test"),
    )
    async def fail_reports(*_args, **_kwargs):
        raise reconcile.report_subscription.ReportSubscriptionError("report_public_verification_failed")
    monkeypatch.setattr(reconcile.report_subscription, "dispatch_if_enabled", fail_reports)
    monkeypatch.setattr(reconcile, "verified_snapshot", lambda _snapshot, _identity: True)
    monkeypatch.setattr(reconcile.research_subscription, "dispatch_after_release", lambda **_kwargs: {"status": "completed"})
    monkeypatch.setattr(reconcile.notice_subscription, "dispatch_after_release", lambda **_kwargs: {"status": "completed"})
    result = await reconcile.reconcile_once(service, root=tmp_path, astro_dir=astro)
    assert result["reports"]["status"] == "failed"
    assert result["research"]["status"] == "completed"
    assert result["notices"]["status"] == "completed"
