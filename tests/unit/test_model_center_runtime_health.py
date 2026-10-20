from datetime import timedelta
from types import SimpleNamespace

from src.ai import model_center
from src.ai.model_center import (
    _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD,
    RuntimeRouteAdmissionController,
    _combined_route_runtime_health_state,
    _provider_check_result_from_model_results,
    _route_routing_status,
    _runtime_admission_settings,
    _runtime_failure_kind,
    _runtime_health_state,
    _utcnow,
    _write_runtime_failure,
    _write_runtime_success,
)


def test_repeated_runtime_timeouts_open_model_circuit_and_success_records_latency() -> None:
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
    assert health["runtime_failure_streak_raw"] == _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD
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
        _runtime_failure_kind(RuntimeError("Model returned an empty completion response"))
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
        assert not _runtime_health_state(payload, now + timedelta(seconds=index))["runtime_failure_active"]

    payload = _write_runtime_failure(
        payload,
        kind="timeout",
        error="request timed out",
        occurred_at=now + timedelta(seconds=2),
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    assert _runtime_health_state(payload, now + timedelta(seconds=2))["runtime_failure_active"]


def test_degraded_routes_receive_bounded_automatic_recovery_probes(monkeypatch) -> None:
    async def exercise() -> None:
        model_center._recovery_probe_attempted_at.clear()

        async def routes():
            return [
                {
                    "model_id": 11,
                    "model_key": "provider:recover-me",
                    "priority": 100,
                    "has_api_key": True,
                    "runtime_degraded": True,
                    "runtime_failure_active": False,
                    "rate_limit_active": False,
                    "runtime_last_failure_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "model_id": 12,
                    "model_key": "provider:still-cooling",
                    "priority": 100,
                    "has_api_key": True,
                    "runtime_degraded": True,
                    "runtime_failure_active": True,
                    "rate_limit_active": False,
                },
            ]

        checked = []

        async def check(model_id, *, structured=True):
            checked.append((model_id, structured))
            return {"success": True, "status": "available", "message": "recovered"}

        monkeypatch.setattr(model_center, "get_runtime_routes", routes)
        monkeypatch.setattr(model_center, "check_model_by_id", check)

        first = await model_center.probe_degraded_model_routes(
            limit=2,
            minimum_interval_seconds=30,
        )
        second = await model_center.probe_degraded_model_routes(
            limit=2,
            minimum_interval_seconds=30,
        )

        assert checked == [(11, True)]
        assert first[0]["success"] is True
        assert second == []

    import asyncio

    asyncio.run(exercise())


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


def test_runtime_admission_balances_providers_and_earns_parallelism() -> None:
    async def exercise() -> None:
        controller = RuntimeRouteAdmissionController()
        centos_fast = {
            "provider_id": 1,
            "model_id": 11,
            "extra_config": {"runtime_max_concurrency": 2},
            "extra_params": {},
        }
        centos_second = {
            "provider_id": 1,
            "model_id": 12,
            "extra_config": {"runtime_max_concurrency": 2},
            "extra_params": {},
        }
        qwen = {
            "provider_id": 2,
            "model_id": 21,
            "extra_config": {"runtime_max_concurrency": 1},
            "extra_params": {},
        }

        first = await controller.acquire(centos_fast)
        assert controller.score(qwen) < controller.score(centos_second)
        await first.release(success=True)

        second = await controller.acquire(centos_fast)
        await second.release(success=True)
        assert controller.snapshot(centos_fast)["runtime_provider_capacity"] == 2

        left = await controller.acquire(centos_fast)
        right = await controller.acquire(centos_second)
        snapshot = controller.snapshot(centos_fast)
        assert snapshot["runtime_provider_inflight"] == 2
        await left.release(success=True)
        await right.release(success=True)

    import asyncio

    asyncio.run(exercise())


def test_runtime_admission_waits_for_capacity_without_raising_a_model_timeout() -> None:
    async def exercise() -> None:
        controller = RuntimeRouteAdmissionController()
        route = {
            "provider_id": 1,
            "model_id": 11,
            "extra_config": {"runtime_max_concurrency": 1},
            "extra_params": {},
        }

        first = await controller.acquire(route)
        second_task = asyncio.create_task(controller.acquire(route))
        await asyncio.sleep(0)
        assert second_task.done() is False

        await first.release(success=True)
        second = await asyncio.wait_for(second_task, timeout=0.1)
        await second.release(success=True)

    import asyncio

    asyncio.run(exercise())


def test_runtime_admission_uses_provider_specific_auto_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.ai.model_center._runtime_admission_global_ceiling",
        lambda: 6,
    )
    automatic = _runtime_admission_settings(
        {
            "runtime_provider_auto_max_concurrency": 5,
            "extra_config": {},
            "extra_params": {},
        }
    )
    configured = _runtime_admission_settings(
        {
            "runtime_provider_auto_max_concurrency": 5,
            "extra_config": {"runtime_max_concurrency": 3},
            "extra_params": {},
        }
    )

    assert automatic["provider_maximum"] == 5
    assert automatic["provider_auto"] is True
    assert configured["provider_maximum"] == 3
    assert configured["provider_auto"] is False


def test_runtime_admission_learns_and_backs_off_each_provider_independently() -> None:
    async def exercise() -> None:
        controller = RuntimeRouteAdmissionController()
        fast = {
            "provider_id": 1,
            "model_id": 11,
            "extra_config": {
                "runtime_max_concurrency": 6,
                "runtime_successes_to_scale_up": 1,
                "runtime_concurrency_adjust_seconds": 20,
            },
            "extra_params": {"runtime_max_concurrency": 6},
        }
        cautious = {
            "provider_id": 2,
            "model_id": 21,
            "extra_config": {
                "runtime_max_concurrency": 3,
                "runtime_successes_to_scale_up": 1,
            },
            "extra_params": {"runtime_max_concurrency": 3},
        }

        for _ in range(5):
            permit = await controller.acquire(fast)
            await permit.release(success=True)
        for _ in range(2):
            permit = await controller.acquire(cautious)
            await permit.release(success=True)

        assert controller.snapshot(fast)["runtime_provider_capacity"] == 6
        assert controller.snapshot(cautious)["runtime_provider_capacity"] == 3
        provider_snapshots = controller.provider_snapshots()
        assert provider_snapshots["1"]["runtime_provider_capacity"] == 6
        assert provider_snapshots["2"]["runtime_provider_capacity"] == 3

        pressured = await controller.acquire(fast)
        await pressured.release(success=False, error=RuntimeError("429 Too Many Requests"))

        fast_snapshot = controller.snapshot(fast)
        assert fast_snapshot["runtime_provider_capacity"] == 6
        assert fast_snapshot["runtime_provider_backoff_remaining_seconds"] == 0
        assert controller.snapshot(cautious)["runtime_provider_capacity"] == 3

        timed_out = await controller.acquire(fast)
        await timed_out.release(success=False, error=TimeoutError("request timed out"))

        fast_snapshot = controller.snapshot(fast)
        assert fast_snapshot["runtime_provider_capacity"] == 6
        assert fast_snapshot["runtime_provider_backoff_remaining_seconds"] == 0

        pressured = await controller.acquire(fast)
        await pressured.release(success=False, error=RuntimeError("provider server busy"))

        fast_snapshot = controller.snapshot(fast)
        assert fast_snapshot["runtime_provider_capacity"] == 3
        assert fast_snapshot["runtime_provider_backoff_remaining_seconds"] > 0
        assert controller.snapshot(cautious)["runtime_provider_capacity"] == 3

        # A malformed response belongs to this model, not to the shared
        # provider quota, so it must not reduce sibling-model concurrency.
        malformed = await controller.acquire(fast)
        await malformed.release(success=False, error=ValueError("invalid structured output"))
        assert controller.snapshot(fast)["runtime_provider_capacity"] == 3

        # Successful calls during the hold-down window do not immediately
        # reopen the capacity that just triggered provider pressure.
        recovering = await controller.acquire(fast)
        await recovering.release(success=True)
        assert controller.snapshot(fast)["runtime_provider_capacity"] == 3

    import asyncio

    asyncio.run(exercise())


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
