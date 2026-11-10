"""The shared catalogue contracts operate without worker-local admission state."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.ai import model_center
from src.ai.catalogue.errors import (
    FailureKind,
    classify_failure,
    extract_retry_after_seconds,
)
from src.ai.catalogue.probes import client_session
from src.ai.catalogue.routes import (
    RouteCapacity,
    build_runtime_routes,
    recovery_probe_candidates,
)


class ProviderError(RuntimeError):
    def __init__(self, message, status, *, nested=False, headers=None):
        super().__init__(message)
        self.status_code = None if nested else status
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


@pytest.mark.parametrize(
    ("error", "kind", "retryable", "scope"),
    [
        (
            ProviderError("quota access denied", 401),
            FailureKind.AUTHENTICATION,
            False,
            "provider",
        ),
        (
            ProviderError("model access denied", 403),
            FailureKind.MODEL_UNAVAILABLE,
            False,
            "model",
        ),
        (
            ProviderError("too many requests", 429),
            FailureKind.RATE_LIMIT,
            True,
            "model",
        ),
        (
            ProviderError("no available channel; retry later", 503),
            FailureKind.CHANNEL_UNAVAILABLE,
            True,
            "model",
        ),
        (
            ProviderError("server failed", 503, nested=True),
            FailureKind.UPSTREAM,
            True,
            "model",
        ),
        (TimeoutError(), FailureKind.TIMEOUT, True, "model"),
        (ConnectionError(), FailureKind.CONNECTION, True, "model"),
        (
            ProviderError("invalid request body", 422),
            FailureKind.INVALID_REQUEST,
            False,
            "request",
        ),
        (RuntimeError("invalid generated JSON"), FailureKind.UNKNOWN, False, "request"),
    ],
)
def test_failure_contract_separates_scope_from_retry_policy(
    error, kind, retryable, scope
):
    failure = classify_failure(error)
    assert (failure.kind, failure.retryable, failure.scope) == (kind, retryable, scope)


def test_retry_after_honors_fractional_delays_and_http_dates():
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    fractional = ProviderError("busy", 429, headers={"retry-after": "1.7"})
    dated = ProviderError(
        "busy", 429, headers={"retry-after": "Tue, 10 Nov 2026 12:00:17 GMT"}
    )
    assert classify_failure(fractional).retry_after_seconds == 2
    assert extract_retry_after_seconds(dated, now=now) == 17


@pytest.mark.parametrize(
    ("params", "provider_limit", "expected"),
    [
        ({}, None, (1, 2)),
        ({"runtime_max_concurrency": 0}, 0, (1, 1)),
        ({"runtime_max_concurrency": "bad"}, "bad", (1, 2)),
        ({"runtime_max_concurrency": 99999}, 99999, (512, 4096)),
        ({"runtime_max_concurrency": 4}, 9, (4, 9)),
    ],
)
def test_capacity_is_bounded_configuration_without_process_occupancy(
    params, provider_limit, expected
):
    capacity = RouteCapacity.from_route(
        {
            "extra_params": params,
            "runtime_provider_auto_max_concurrency": provider_limit,
        }
    )
    assert (capacity.model, capacity.provider) == expected
    assert capacity.snapshot()["runtime_model_inflight"] == 0
    assert capacity.snapshot()["runtime_provider_inflight"] == 0


def _models(now):
    provider = SimpleNamespace(
        id=5,
        provider_key="shared",
        provider_name="custom",
        api_style="openai_compatible",
        api_key="configured-test-key",
        base_url=None,
        extra_config={},
        extra_headers={},
        last_check_status="available",
    )

    def model(identifier, params, priority):
        return SimpleNamespace(
            id=identifier,
            provider=provider,
            model_key=f"shared:{identifier}",
            model_name=f"model-{identifier}",
            priority=priority,
            api_style=None,
            extra_params=params,
            temperature=None,
            last_check_status="available",
            last_checked_at=now,
        )

    return [
        model(
            1,
            {
                "runtime_max_concurrency": 3,
                "routing_state": {
                    "cooldown_until": (now + timedelta(seconds=30)).isoformat(),
                },
            },
            0,
        ),
        model(2, {"runtime_max_concurrency": 4}, 1),
    ]


def test_route_snapshot_uses_one_clock_and_keeps_sibling_capacity_independent():
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    records = _models(now)
    routes = build_runtime_routes(records, now=now)
    assert [r["model_id"] for r in routes] == [1, 2]  # priority zero remains first
    assert [r["available_for_routing"] for r in routes] == [False, True]
    assert [r["runtime_model_capacity"] for r in routes] == [3, 4]
    assert [r["runtime_provider_capacity"] for r in routes] == [7, 7]
    assert routes[0]["rate_limit_remaining_seconds"] == 30
    assert build_runtime_routes(records, now=now + timedelta(seconds=31))[0][
        "available_for_routing"
    ]


async def test_catalogue_projection_runs_after_database_session_is_released(
    monkeypatch,
):
    in_session = False
    records = _models(datetime.now(timezone.utc))

    class DB:
        async def execute(self, statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: records))

    @asynccontextmanager
    async def database():
        nonlocal in_session
        in_session = True
        yield DB()
        in_session = False

    async def boot(**kwargs):
        pass

    def project(rows):
        assert not in_session
        assert rows is records
        return [{"model_key": "projected"}]

    monkeypatch.setattr(model_center, "get_db", database)
    monkeypatch.setattr(model_center, "bootstrap_model_center_from_env", boot)
    monkeypatch.setattr(model_center.routes, "build_runtime_routes", project)
    assert await model_center.get_runtime_routes() == [{"model_key": "projected"}]


def test_recovery_due_time_survives_worker_restart_and_is_bounded():
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    base = {
        "has_api_key": True,
        "runtime_degraded": True,
        "runtime_last_failure_at": (now - timedelta(minutes=20)).isoformat(),
    }
    routes = [
        {
            **base,
            "model_id": 1,
            "last_checked_at": (now - timedelta(seconds=100)).isoformat(),
        },
        {**base, "model_id": 2, "runtime_failure_active": True},
        {**base, "model_id": 3, "routing_status": "outside_window"},
        {**base, "model_id": 4},
        {**base, "model_id": 5},
    ]
    assert [
        r["model_id"] for r in recovery_probe_candidates(routes, now=now, limit=1)
    ] == [4]
    assert [
        r["model_id"]
        for r in recovery_probe_candidates(routes, now=now + timedelta(minutes=5))
    ] == [1, 4]


@pytest.mark.parametrize(
    "error", [None, RuntimeError("request failed"), asyncio.CancelledError()]
)
async def test_probe_client_pool_closes_on_success_failure_and_cancellation(error):
    closed = []

    class Client:
        async def close(self):
            closed.append(True)

    async def operation():
        async with client_session(Client):
            if error is not None:
                raise error

    if error is None:
        await operation()
    else:
        with pytest.raises(type(error)):
            await operation()
    assert closed == [True]
