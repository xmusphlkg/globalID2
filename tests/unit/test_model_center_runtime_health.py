from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.ai import model_center
from src.ai.catalogue.errors import _runtime_failure_kind
from src.ai.catalogue.health import (
    _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD,
    _combined_route_runtime_health_state,
    _provider_check_result_from_model_results,
    _route_routing_status,
    _runtime_health_state,
    _runtime_success_is_due,
    _utcnow,
    _write_runtime_failure,
    _write_runtime_success,
)


@pytest.mark.parametrize("message,retry_after,expected", [
    ("Insufficient user quota, remaining quota: -0.000516", None, 900),
    ("{'error': {'code': 'insufficient_user_quota'}}", 10, 900),
    ("insufficient_quota", 1800, 1800),
    ('upstream rate limit reached; retry in 22s - {"code":14018,"msg":"额度已用尽，请购买加量包"}', 22, 900),
    ('upstream rate limit reached; retry in 9s - {"code":14012,"msg":"个人额度已耗尽，将于周期结束后自动重置"}', 9, 900),
    ("Too many requests", None, 10),
    ("Per-minute quota exceeded", 60, 60),
    ("每分钟请求额度超限，请稍后重试", 10, 10),
])
async def test_account_credit_exhaustion_parks_only_affected_model(
    monkeypatch, message, retry_after, expected
):
    now = _utcnow()
    model = SimpleNamespace(extra_params={}, last_check_status="available")
    sibling = SimpleNamespace(extra_params={}, last_check_status="available")

    class DB:
        async def get(self, entity, model_id, *, with_for_update):
            assert model_id == 173 and with_for_update
            return model

        async def commit(self):
            pass

    @asynccontextmanager
    async def database():
        yield DB()

    monkeypatch.setattr(model_center, "get_db", database)
    monkeypatch.setattr(model_center.health, "_utcnow", lambda: now)
    result = await model_center.mark_route_rate_limited(
        {"model_id": 173, "extra_params": {"rate_limit_cooldown_seconds": 10}},
        message,
        retry_after_seconds=retry_after,
    )
    assert result["cooldown_seconds"] == expected
    assert sibling.last_check_status == "available"
    assert sibling.extra_params == {}
    state = model_center.health._rate_limit_state
    assert state(model.extra_params, now)["rate_limit_remaining_seconds"] == expected
    assert not state(model.extra_params, now + timedelta(seconds=expected + 1))[
        "rate_limit_active"
    ]
    model.last_check_status = "available"
    model.extra_params = model_center.health._clear_payload_rate_limit(
        model.extra_params, now + timedelta(seconds=1)
    )
    assert not state(model.extra_params, now + timedelta(seconds=1))["rate_limit_active"]


@pytest.mark.parametrize("probe_offset,recovered", [(-1, False), (1, True)])
async def test_probe_started_before_new_limit_cannot_erase_its_cooldown(
    monkeypatch, probe_offset, recovered
):
    limited_at = _utcnow()
    now = limited_at + timedelta(seconds=3)
    payload = model_center.health._mark_payload_rate_limited(
        {}, limited_at + timedelta(seconds=900), limited_at
    )
    model = SimpleNamespace(
        extra_params=payload,
        last_check_status="rate_limited",
        last_check_message="Insufficient user quota",
        last_checked_at=limited_at,
    )

    class DB:
        async def get(self, entity, model_id, *, with_for_update):
            assert model_id == 173 and with_for_update
            return model

        async def commit(self):
            pass

    @asynccontextmanager
    async def database():
        yield DB()

    monkeypatch.setattr(model_center, "get_db", database)
    monkeypatch.setattr(model_center.health, "_utcnow", lambda: now)
    await model_center.update_model_check_result(
        173, "available", "Successful probe",
        probe_started_at=limited_at + timedelta(seconds=probe_offset),
    )
    state = model_center.health._rate_limit_state(model.extra_params, now)
    assert state["rate_limit_active"] == (not recovered)
    assert model.last_check_status == ("available" if recovered else "rate_limited")
    if not recovered:
        assert model.last_check_message == "Insufficient user quota"
        assert model.extra_params == payload


def test_runtime_success_does_not_clear_account_quota_cooldown():
    now = _utcnow()
    payload = model_center.health._mark_payload_rate_limited(
        {}, now + timedelta(seconds=900), now
    )
    success = _write_runtime_success(
        payload, occurred_at=now + timedelta(seconds=1), duration_seconds=1,
    )
    assert model_center.health._rate_limit_state(success, now)["rate_limit_active"]
    assert success["routing_state"]["cooldown_until"] == payload["routing_state"]["cooldown_until"]


async def test_inflight_success_cannot_clear_newer_quota_generation(monkeypatch):
    now = _utcnow()
    previous = model_center.health._mark_payload_rate_limited(
        {}, now, now - timedelta(seconds=10)
    )
    current = model_center.health._mark_payload_rate_limited(
        previous, now + timedelta(seconds=900), now
    )

    class DB:
        async def execute(self, statement):
            return SimpleNamespace(one_or_none=lambda: (current, "rate_limited"))

        async def scalar(self, statement):
            pytest.fail("A stale success must be rejected before locking the model")

        async def commit(self):
            pytest.fail("A stale success must not clear the new cooldown")

    @asynccontextmanager
    async def database():
        yield DB()

    monkeypatch.setattr(model_center, "get_db", database)
    monkeypatch.setattr(model_center.health, "_utcnow", lambda: now)
    await model_center.clear_route_rate_limit({
        "model_id": 173,
        "last_check_status": "rate_limited",
        "extra_params": previous,
    })
    assert model_center.health._rate_limit_state(current, now)["rate_limit_remaining_seconds"] == 900


def test_success_sampling_recovers_failures_but_rejects_older_successes():
    now = _utcnow()
    healthy = _write_runtime_success({}, occurred_at=now, duration_seconds=1)
    assert not _runtime_success_is_due(healthy, now + timedelta(seconds=4))
    assert _runtime_success_is_due(healthy, now + timedelta(seconds=5))
    failed = _write_runtime_failure(
        healthy,
        kind="timeout",
        error="timeout",
        occurred_at=now + timedelta(seconds=1),
        duration_seconds=1,
        cooldown_seconds=30,
    )
    assert not _runtime_success_is_due(failed, now)
    assert _runtime_success_is_due(failed, now + timedelta(seconds=2))


def test_sampled_success_count_uses_durable_calls_and_never_decreases():
    now = _utcnow()
    counted = _write_runtime_success(
        {}, occurred_at=now, duration_seconds=1, observed_success_count=50
    )
    assert _runtime_health_state(counted)["runtime_success_count"] == 50
    older_count = _write_runtime_success(
        counted,
        occurred_at=now + timedelta(seconds=5),
        duration_seconds=1,
        observed_success_count=49,
    )
    assert _runtime_health_state(older_count)["runtime_success_count"] == 50


def test_upstream_retry_after_survives_success_until_instructed_deadline():
    now = _utcnow()
    payload = {}
    for index in range(3):
        at = now + timedelta(seconds=index)
        payload = _write_runtime_failure(
            payload,
            kind="upstream",
            error="edge unavailable",
            occurred_at=at,
            duration_seconds=1,
            cooldown_seconds=60,
            retry_after_seconds=600,
        )
        assert _runtime_health_state(payload, at)["runtime_failure_active"] == (
            index == 2
        )
    assert _runtime_health_state(payload, now + timedelta(seconds=200))[
        "runtime_failure_active"
    ]
    recovered = _write_runtime_success(
        payload, occurred_at=now + timedelta(seconds=201), duration_seconds=1
    )
    health = _runtime_health_state(recovered, now + timedelta(seconds=201))
    assert health["runtime_failure_streak"] == 0
    assert health["runtime_failure_active"]
    assert health["runtime_failure_remaining_seconds"] == 401
    assert not _runtime_health_state(recovered, now + timedelta(seconds=603))[
        "runtime_failure_active"
    ]
    after = _write_runtime_success(
        recovered, occurred_at=now + timedelta(seconds=603), duration_seconds=1
    )
    assert "runtime_retry_after_until" not in after["routing_state"]


def test_repeated_runtime_timeouts_open_model_circuit_and_success_records_latency() -> (
    None
):
    now = _utcnow()
    failed = {}
    for index in range(3):
        failed = _write_runtime_failure(
            failed,
            kind="timeout",
            error="request timed out",
            occurred_at=now + timedelta(seconds=index),
            duration_seconds=12.5,
            cooldown_seconds=30,
        )
    health = _runtime_health_state(failed, now + timedelta(seconds=2))

    assert health["runtime_failure_active"] is True
    assert health["runtime_failure_kind"] == "timeout"
    assert health["runtime_failure_streak"] == 3
    assert health["runtime_timeout_count"] == 3
    assert health["runtime_last_latency_ms"] == 12500.0

    recovered = _write_runtime_success(
        failed,
        occurred_at=now + timedelta(seconds=3),
        duration_seconds=0.8,
    )
    recovered_health = _runtime_health_state(recovered, now + timedelta(seconds=3))
    assert recovered_health["runtime_failure_active"] is False
    assert recovered_health["runtime_failure_streak"] == 0
    assert recovered_health["runtime_success_count"] == 1
    assert recovered_health["runtime_latency_ewma_ms"] == 800.0


def test_provider_runtime_cooldown_does_not_block_healthy_sibling_model() -> None:
    now = _utcnow()
    provider_state = {}
    for index in range(3):
        provider_state = _write_runtime_failure(
            provider_state,
            kind="connection",
            error="connection reset",
            occurred_at=now + timedelta(seconds=index),
            duration_seconds=1.0,
            cooldown_seconds=60,
        )
    model = SimpleNamespace(extra_params={})
    provider = SimpleNamespace(extra_config=provider_state)

    combined = _combined_route_runtime_health_state(model, provider)

    assert combined["runtime_failure_active"] is False
    assert combined["runtime_failure_scope"] is None
    assert combined["runtime_failure_kind"] is None


def test_chronic_model_failures_keep_route_out_of_active_candidates() -> None:
    now = _utcnow()
    payload = {}
    for index in range(_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD):
        payload = _write_runtime_failure(
            payload,
            kind="timeout",
            error="request timed out",
            occurred_at=now - timedelta(minutes=11) + timedelta(seconds=index),
            duration_seconds=35.0,
            cooldown_seconds=30,
        )

    model = SimpleNamespace(extra_params=payload)
    provider = SimpleNamespace(extra_config={})
    combined = _combined_route_runtime_health_state(model, provider)

    assert combined["runtime_failure_active"] is False
    assert combined["runtime_degraded"] is True
    assert combined["runtime_degraded_scope"] == "model"
    assert combined["runtime_degraded_reason"] == "chronic_model_failure_streak"

    recovered = _write_runtime_success(payload, occurred_at=now, duration_seconds=1.0)
    recovered_combined = _combined_route_runtime_health_state(
        SimpleNamespace(extra_params=recovered),
        provider,
    )
    assert recovered_combined["runtime_degraded"] is False


def test_quiet_runtime_failures_decay_without_erasing_history() -> None:
    now = _utcnow()
    payload = {}
    occurred_at = now - timedelta(hours=4, minutes=5)
    for index in range(_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD):
        payload = _write_runtime_failure(
            payload,
            kind="timeout",
            error="request timed out",
            occurred_at=occurred_at + timedelta(seconds=index),
            duration_seconds=30.0,
            cooldown_seconds=30,
        )

    health = _runtime_health_state(payload, now)

    assert health["runtime_failure_streak"] == 0
    assert (
        health["runtime_failure_streak_raw"] == _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD
    )
    assert health["runtime_failure_count"] == _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD


def test_route_status_distinguishes_probe_success_from_runtime_cooldown() -> None:
    status, reason = _route_routing_status(
        {"has_api_key": True},
        status_routable=True,
        rate_limit_state={"rate_limit_active": False},
        runtime_health_state={
            "runtime_failure_active": True,
            "runtime_degraded": False,
        },
    )

    assert status == "cooling_down"
    assert "production workload failure" in reason


def test_empty_model_output_is_not_a_route_health_failure() -> None:
    assert (
        _runtime_failure_kind(
            RuntimeError("Model returned an empty completion response")
        )
        is None
    )


def test_consecutive_transport_failures_are_required_before_route_cooldown() -> None:
    now = _utcnow()
    payload = {}
    for index in range(2):
        payload = _write_runtime_failure(
            payload,
            kind="timeout",
            error="request timed out",
            occurred_at=now + timedelta(seconds=index),
            duration_seconds=1.0,
            cooldown_seconds=60,
        )
        assert not _runtime_health_state(payload, now + timedelta(seconds=index))[
            "runtime_failure_active"
        ]

    payload = _write_runtime_failure(
        payload,
        kind="timeout",
        error="request timed out",
        occurred_at=now + timedelta(seconds=2),
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    assert _runtime_health_state(payload, now + timedelta(seconds=2))[
        "runtime_failure_active"
    ]


async def test_degraded_routes_receive_bounded_persisted_recovery_probes(
    monkeypatch,
) -> None:
    now = _utcnow()
    route = {
        "model_id": 11,
        "model_key": "provider:recover-me",
        "priority": 100,
        "has_api_key": True,
        "runtime_degraded": True,
        "runtime_failure_active": False,
        "rate_limit_active": False,
        "runtime_last_failure_at": (now - timedelta(minutes=10)).isoformat(),
    }

    async def routes():
        return [dict(route), {**route, "model_id": 12, "runtime_failure_active": True}]

    checked = []

    async def check(model_id, *, structured=True):
        checked.append((model_id, structured))
        # The real service persists this timestamp for success and failure.
        route["last_checked_at"] = now.isoformat()
        return {
            "success": False,
            "status": "probe_failed",
            "message": "still recovering",
        }

    monkeypatch.setattr(model_center, "get_runtime_routes", routes)
    monkeypatch.setattr(model_center, "check_model_by_id", check)

    first = await model_center.probe_degraded_model_routes(
        limit=2, minimum_interval_seconds=30
    )
    second = await model_center.probe_degraded_model_routes(
        limit=2, minimum_interval_seconds=30
    )

    assert checked == [(11, True)]
    assert first[0]["status"] == "probe_failed"
    assert second == []


def test_legacy_structured_output_cooldown_does_not_block_route() -> None:
    now = _utcnow()
    payload = {
        "routing_state": {
            "last_runtime_failure_kind": "structured_output",
            "last_runtime_failure_at": now.isoformat(),
            "runtime_failure_streak": 6,
            "runtime_cooldown_until": (now + timedelta(minutes=10)).isoformat(),
        }
    }
    health = _runtime_health_state(payload, now)

    assert health["runtime_failure_active"] is False
    assert health["runtime_failure_streak"] == 0
    assert health["runtime_failure_streak_raw"] == 6


def test_provider_check_stays_available_when_any_sibling_model_succeeds() -> None:
    status, message = _provider_check_result_from_model_results(
        [
            {
                "success": False,
                "status": "unavailable",
                "model_name": "bad-model",
                "message": "model does not exist or you do not have access",
            },
            {
                "success": True,
                "status": "available",
                "model_name": "good-model",
                "message": "Structured workload probe successful",
            },
        ]
    )

    assert status == "available"
    assert message == "Structured workload probe successful"


def test_provider_check_does_not_apply_model_rate_limit_to_provider_status() -> None:
    status, message = _provider_check_result_from_model_results(
        [
            {
                "success": False,
                "status": "unavailable",
                "model_name": "bad-model",
                "message": "model unavailable",
            },
            {
                "success": False,
                "status": "rate_limited",
                "model_name": "limited-model",
                "message": "quota exhausted",
            },
        ]
    )

    assert status == "probe_failed"
    assert message == "quota exhausted"
