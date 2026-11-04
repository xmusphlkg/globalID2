"""Independently reconcile enabled subscription outboxes against published data.

This loop never rebuilds or publishes the website.  It works from the retained
Pages snapshot and published reports bundle, and every dispatcher still checks
its activation watermark, public release identity, and immutable outbox.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from loguru import logger

from . import notice_subscription, report_subscription, research_subscription
from .shared_assets import verified_snapshot

TRUE = {"1", "true", "yes", "on"}
POLL_SECONDS = 15 * 60


def enabled(service: Any) -> bool:
    return any(service._env_value(name, "false").lower() in TRUE for name in (
        "SUBSCRIPTIONS__REPORT_DIGEST_ENABLED", "SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED", "SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED",
    ))


def _error(exc: Exception) -> dict[str, str]:
    known = (report_subscription.ReportSubscriptionError, research_subscription.ResearchSubscriptionError,
             notice_subscription.NoticeSubscriptionError)
    return {"status": "failed", "error_code": str(exc) if isinstance(exc, known) else "internal_error"}


async def dispatch_safely(name: str, dispatcher: Any, **kwargs: Any) -> dict[str, Any]:
    """Record a failed subscription dispatch without failing an already-live release."""
    try:
        return await asyncio.to_thread(dispatcher, **kwargs)
    except Exception as exc:
        result = _error(exc)
        logger.error(json.dumps({"event": "subscription_dispatch_failed", "stream": name,
                                 "error_code": result["error_code"], "error_type": type(exc).__name__}))
        return result


async def reconcile_once(service: Any, *, root: Path, astro_dir: Path) -> dict[str, Any]:
    env = service._env_value
    runtime = SimpleNamespace(root_dir=root, astro_dir=astro_dir)
    results: dict[str, Any] = {}
    if env("SUBSCRIPTIONS__REPORT_DIGEST_ENABLED", "false").lower() in TRUE:
        origin = str(service._config().r2_reports_public_base_url or "https://reports.globalinfectiousdisease.com").rstrip("/")
        try:
            results["reports"] = await report_subscription.dispatch_if_enabled(service, runtime, reports_origin=origin)
        except Exception as exc:
            results["reports"] = _error(exc)
            logger.error(json.dumps({"event": "subscription_dispatch_failed", "stream": "reports",
                                     "error_code": results["reports"]["error_code"], "error_type": type(exc).__name__}))

    research = env("SUBSCRIPTIONS__RESEARCH_DIGEST_ENABLED", "false").lower() in TRUE
    notice = env("SUBSCRIPTIONS__NOTICE_ALERTS_ENABLED", "false").lower() in TRUE
    if research or notice:
        snapshot = root / "exports" / "verified-pages-snapshot"
        try:
            identity = json.loads((snapshot / "release.json").read_text(encoding="utf-8"))
            if not isinstance(identity, dict) or not await asyncio.to_thread(verified_snapshot, snapshot, identity):
                raise ValueError("verified_snapshot_unavailable")
        except (OSError, ValueError, TypeError):
            for key, active in (("research", research), ("notices", notice)):
                if active:
                    results[key] = {"status": "failed", "error_code": "verified_snapshot_unavailable"}
        else:
            # The dispatchers expect astro_dir/dist and, for research, the
            # source brief. The brief is accepted only if its hash is embedded
            # in both retained published pages; it cannot silently drift.
            with tempfile.TemporaryDirectory(prefix="gids-subscription-reconcile-") as temp:
                workspace = Path(temp)
                (workspace / "dist").symlink_to(snapshot.resolve(), target_is_directory=True)
                (workspace / "src").symlink_to((astro_dir / "src").resolve(), target_is_directory=True)
                common = dict(root=root, astro_dir=workspace, identity=identity,
                              worker_base_url=env("SUBSCRIPTIONS__PUBLIC_BASE_URL"),
                              public_base_url=env("GIDS_PUBLIC_BASE_URL", "https://globalinfectiousdisease.com"),
                              mode=env("SUBSCRIPTIONS__DELIVERY_MODE", "pilot").strip().lower(), guard_activation=True)
                for key, active, dispatcher, args in (
                    ("research", research, research_subscription.dispatch_after_release,
                     {"token": env("SUBSCRIPTIONS__ADMIN_API_TOKEN"), "start_week": env("SUBSCRIPTIONS__RESEARCH_DIGEST_START_WEEK")}),
                    ("notices", notice, notice_subscription.dispatch_after_release,
                     {"token": env("SUBSCRIPTIONS__SITUATION_ALERT_INGEST_TOKEN"), "start_date": env("SUBSCRIPTIONS__NOTICE_ALERTS_START_DATE")}),
                ):
                    if not active:
                        continue
                    results[key] = await dispatch_safely(key, dispatcher, **common, **args)
    logger.info(json.dumps({"event": "subscription_reconciliation", "results": results}, ensure_ascii=False))
    return results


async def run_loop(service: Any, *, root: Path, astro_dir: Path, stop: asyncio.Event,
                   interval_seconds: float = POLL_SECONDS) -> None:
    while not stop.is_set():
        try:
            if enabled(service):
                await reconcile_once(service, root=root, astro_dir=astro_dir)
        except Exception as exc:
            logger.error(json.dumps({"event": "subscription_reconciliation_failed", "error_type": type(exc).__name__}))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
