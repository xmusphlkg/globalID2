"""Centralized, read-only Cloudflare observations for the publication dashboard.

Adaptive Workers analytics are estimates, not a billing statement. Missing
billing permissions must never turn a daily observation into a claimed plan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "https://api.cloudflare.com/client/v4/"
QUERY = """query($account: string, $start: string, $end: string) {
  viewer { accounts(filter: {accountTag: $account}) {
    total: workersInvocationsAdaptive(limit: 1,
      filter: {datetime_geq: $start, datetime_lt: $end}) { sum { requests } }
    services: workersInvocationsAdaptive(limit: 1000,
      filter: {datetime_geq: $start, datetime_lt: $end}) {
      dimensions { scriptName } sum { requests }
    }
  } }
}"""


def _safe_error(exc: Exception, fallback: str) -> str:
    # urllib can include the entire Authorization value in a ValueError for a
    # malformed header. Only our bounded diagnostic vocabulary may be saved.
    code = str(exc)
    allowed = {"cloudflare_analytics_query_failed", "cloudflare_analytics_account_unavailable",
               "cloudflare_analytics_invalid_count", "cloudflare_analytics_invalid_service",
               "cloudflare_service_breakdown_truncated", "cloudflare_analytics_invalid_response"}
    return code if code in allowed or re.fullmatch(r"cloudflare_http_[1-5]\d{2}", code) else fallback


def request_json(path: str, token: str, payload: dict | None = None) -> dict:
    request = Request(API + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except HTTPError as exc:
        # Never serialize request headers, credentials or raw Cloudflare errors.
        raise ValueError(f"cloudflare_http_{exc.code}") from None


def _period(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def subscription_period(response: dict, now: datetime) -> tuple[str, datetime, datetime]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    unknown = ("unknown", start, start + timedelta(days=1))
    if not response.get("success") or not isinstance(response.get("result"), list):
        return unknown
    # Do not infer Workers Free from a zone's DNS/CDN plan or an empty list.
    matches = [row for row in response["result"] if isinstance(row, dict)
               and row.get("state") in {"active", "paid"}
               and "workers" in str((row.get("rate_plan") or {}).get("public_name", "")).lower()]
    if len(matches) != 1:
        return unknown
    row = matches[0]
    name = str(row["rate_plan"].get("public_name", "")).lower()
    if name == "workers free":
        return "free", start, start + timedelta(days=1)
    begin, end = _period(row.get("current_period_start")), _period(row.get("current_period_end"))
    if name in {"workers paid", "workers bundled", "workers standard"} and begin and end and begin <= now < end:
        return "paid", begin, end
    return unknown


def parse_analytics(response: dict) -> tuple[int | None, list[dict]]:
    if response.get("errors"):
        raise ValueError("cloudflare_analytics_query_failed")
    try:
        accounts = response["data"]["viewer"]["accounts"]
        if len(accounts) != 1:
            raise ValueError("cloudflare_analytics_account_unavailable")
        rows = accounts[0]
        total_rows = rows["total"]
        if len(total_rows) != 1:
            # Empty aggregate is not proof that the account had zero requests.
            return None, []
        def count(row):
            value = row["sum"]["requests"]
            if type(value) is not int or value < 0:
                raise ValueError("cloudflare_analytics_invalid_count")
            return value
        total = count(total_rows[0])
        services = [{"id": row["dimensions"]["scriptName"], "requests": count(row)} for row in rows["services"]]
        if any(not isinstance(row["id"], str) or not row["id"] for row in services):
            raise ValueError("cloudflare_analytics_invalid_service")
        if len(services) >= 1000:
            raise ValueError("cloudflare_service_breakdown_truncated")
        return total, sorted(services, key=lambda row: row["id"])
    except (KeyError, TypeError, IndexError):
        raise ValueError("cloudflare_analytics_invalid_response") from None


def collect_usage(*, token: str, account: str, now: datetime | None = None, fetch=request_json) -> dict:
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    plan, start, end = subscription_period({}, now)
    reasons = []
    total, services = None, []
    if token and account:
        try:
            plan, start, end = subscription_period(fetch(f"accounts/{account}/subscriptions", token), now)
        except Exception as exc:
            reasons.append(_safe_error(exc, "cloudflare_subscription_unavailable"))
        try:
            total, services = parse_analytics(fetch("graphql", token, {
                "query": QUERY, "variables": {"account": account, "start": start.isoformat(), "end": now.isoformat()},
            }))
        except Exception as exc:
            reasons.append(_safe_error(exc, "cloudflare_analytics_unavailable"))
    else:
        reasons.append("cloudflare_credentials_unavailable")
    if plan == "unknown":
        reasons.append("workers_plan_unverified_daily_observation_only")
    if total is None:
        reasons.append("account_usage_unavailable")
    # The initial collector does not claim billing limits or forecasts from
    # adaptive samples. These require separately verified billing metadata.
    return {
        "schema_version": 1, "plan": plan, "observed_at": now.isoformat(),
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "source": "cloudflare_graphql_workersInvocationsAdaptive_estimate",
        "account_total": total, "services": services,
        "limit": None, "remaining": None, "projected_usage": None,
        "reason": "; ".join(reasons) or None,
    }


def write_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".usage-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(snapshot, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def refresh_usage() -> dict:
    from src.services.settings_service import system_settings_service
    from src.core.config import get_config
    config = system_settings_service.cloudflare_runtime()
    snapshot = collect_usage(token=config["cloudflare_api_token"], account=config["cloudflare_account_id"])
    write_snapshot(get_config().data_dir / "cloudflare-publication-usage.json", snapshot)
    return snapshot


async def run_usage_collector(stop_event: asyncio.Event) -> None:
    """Run only in the existing scheduler's singleton lease, never API workers."""
    if os.getenv("GIDS_CLOUDFLARE_USAGE_POLL_ENABLED", "").lower() not in {"1", "true"}:
        return
    from src.core.logging import get_logger
    logger = get_logger(__name__)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(refresh_usage)
        except Exception:
            logger.warning("Publication usage snapshot refresh failed; prior snapshot will age to stale")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass
