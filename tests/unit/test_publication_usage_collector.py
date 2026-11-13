from datetime import datetime, timezone
import asyncio
import json

import pytest

from src.services.data_release.publication_usage import collect_usage, parse_analytics, subscription_period, write_snapshot

NOW = datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc)


def response(total=7158):
    return {"data": {"viewer": {"accounts": [{"total": [{"sum": {"requests": total}}],
             "services": [{"dimensions": {"scriptName": "papers"}, "sum": {"requests": 7000}}]}]}}, "errors": None}


def test_permission_denied_plan_retains_observed_account_total_without_claiming_free():
    def fetch(path, token, payload=None):
        if path.endswith("subscriptions"):
            raise ValueError("cloudflare_http_403")
        assert payload["variables"]["start"] == "2026-11-13T00:00:00+00:00"
        return response()
    snapshot = collect_usage(token="test", account="test", now=NOW, fetch=fetch)
    assert snapshot["plan"] == "unknown"
    assert snapshot["account_total"] == 7158
    assert snapshot["remaining"] is None
    assert "403" in snapshot["reason"]
    assert "estimate" in snapshot["source"]


def test_account_total_is_its_own_aggregate_not_sum_of_services():
    assert parse_analytics(response()) == (7158, [{"id": "papers", "requests": 7000}])
    assert parse_analytics(response(0))[0] == 0
    missing = response()
    missing["data"]["viewer"]["accounts"][0]["total"] = []
    assert parse_analytics(missing)[0] is None


@pytest.mark.parametrize("value", [{"errors": [{"message": "denied"}]}, {}, {"data": None}, response(-1), response(True)])
def test_incomplete_or_invalid_metrics_never_become_zero(value):
    with pytest.raises(ValueError):
        parse_analytics(value)


def test_billing_period_is_only_accepted_from_explicit_workers_subscription():
    unknown = subscription_period({"success": True, "result": []}, NOW)
    assert unknown[0] == "unknown"
    paid = {"state": "active", "rate_plan": {"public_name": "Workers Paid"},
            "current_period_start": "2026-11-05T00:00:00Z", "current_period_end": "2026-12-05T00:00:00Z"}
    plan, start, end = subscription_period({"success": True, "result": [paid]}, NOW)
    assert plan == "paid" and start.day == 5 and end.day == 5
    paid["rate_plan"]["public_name"] = "Free Website"
    assert subscription_period({"success": True, "result": [paid]}, NOW)[0] == "unknown"


def test_no_credentials_makes_no_network_call_and_snapshot_write_is_complete(tmp_path):
    def forbidden(*args):
        raise AssertionError("Network must not be called")
    snapshot = collect_usage(token="", account="", now=NOW, fetch=forbidden)
    assert snapshot["account_total"] is None
    path = tmp_path / "usage.json"
    write_snapshot(path, snapshot)
    assert json.loads(path.read_text()) == snapshot
    assert list(tmp_path.iterdir()) == [path]


def test_transport_errors_never_serialize_authorization_or_other_secrets():
    def malformed_header(*args):
        raise ValueError("Invalid header value b'Bearer secret-token\\n'")
    snapshot = collect_usage(token="secret-token\n", account="test", now=NOW, fetch=malformed_header)
    assert "secret-token" not in json.dumps(snapshot)
    assert "cloudflare_subscription_unavailable" in snapshot["reason"]


@pytest.mark.asyncio
async def test_collector_is_opt_in_and_stops_without_waiting_five_minutes(monkeypatch):
    from src.services.data_release import publication_usage
    stop = asyncio.Event()
    called = asyncio.Event()
    loop = asyncio.get_running_loop()
    def refresh():
        loop.call_soon_threadsafe(called.set)
    monkeypatch.setattr(publication_usage, "refresh_usage", refresh)
    monkeypatch.delenv("GIDS_CLOUDFLARE_USAGE_POLL_ENABLED", raising=False)
    await publication_usage.run_usage_collector(stop)
    assert not called.is_set()
    monkeypatch.setenv("GIDS_CLOUDFLARE_USAGE_POLL_ENABLED", "true")
    task = asyncio.create_task(publication_usage.run_usage_collector(stop))
    await asyncio.wait_for(called.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
