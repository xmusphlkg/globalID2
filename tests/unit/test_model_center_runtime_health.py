from datetime import timedelta
from types import SimpleNamespace

from src.ai import model_center
from src.ai.catalogue.errors import _runtime_failure_kind
from src.ai.catalogue.health import (
    _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD,
    _combined_route_runtime_health_state,
    _provider_check_result_from_model_results,
    _route_routing_status,
    _runtime_health_state,
    _utcnow,
    _write_runtime_failure,
    _write_runtime_success,
)


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
