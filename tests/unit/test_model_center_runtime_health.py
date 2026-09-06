from datetime import timedelta
from types import SimpleNamespace

from src.ai.model_center import (
    _combined_route_runtime_health_state,
    _MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD,
    _provider_check_result_from_model_results,
    _runtime_health_state,
    _utcnow,
    _write_provider_runtime_failure,
    _write_runtime_failure,
    _write_runtime_success,
    RuntimeRouteAdmissionController,
)


def test_runtime_timeout_opens_model_circuit_and_success_records_latency() -> None:
    now = _utcnow()
    failed = _write_runtime_failure(
        {},
        kind="timeout",
        error="request timed out",
        occurred_at=now,
        duration_seconds=12.5,
        cooldown_seconds=30,
    )
    health = _runtime_health_state(failed, now)

    assert health["runtime_failure_active"] is True
    assert health["runtime_failure_kind"] == "timeout"
    assert health["runtime_failure_streak"] == 1
    assert health["runtime_timeout_count"] == 1
    assert health["runtime_last_latency_ms"] == 12500.0

    recovered = _write_runtime_success(
        failed,
        occurred_at=now + timedelta(seconds=1),
        duration_seconds=0.8,
    )
    recovered_health = _runtime_health_state(recovered, now + timedelta(seconds=1))
    assert recovered_health["runtime_failure_active"] is False
    assert recovered_health["runtime_failure_streak"] == 0
    assert recovered_health["runtime_success_count"] == 1
    assert recovered_health["runtime_latency_ewma_ms"] == 800.0


def test_provider_health_can_temporarily_block_an_otherwise_healthy_model() -> None:
    now = _utcnow()
    provider_state = _write_runtime_failure(
        {},
        kind="connection",
        error="connection reset",
        occurred_at=now,
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    model = SimpleNamespace(extra_params={})
    provider = SimpleNamespace(extra_config=provider_state)

    combined = _combined_route_runtime_health_state(model, provider)

    assert combined["runtime_failure_active"] is True
    assert combined["runtime_failure_scope"] == "provider"
    assert combined["runtime_failure_kind"] == "connection"


def test_chronic_model_failures_keep_route_out_of_active_candidates() -> None:
    now = _utcnow()
    payload = {}
    for index in range(_MODEL_CHRONIC_FAILURE_STREAK_THRESHOLD):
        payload = _write_runtime_failure(
            payload,
            kind="timeout",
            error="request timed out",
            occurred_at=now - timedelta(minutes=30, seconds=index),
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


def test_active_provider_circuit_does_not_extend_its_recovery_window() -> None:
    now = _utcnow()
    first, first_open = _write_provider_runtime_failure(
        {},
        kind="connection",
        error="connection reset",
        occurred_at=now,
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    assert first_open is False

    opened, second_open = _write_provider_runtime_failure(
        first,
        kind="connection",
        error="connection reset",
        occurred_at=now + timedelta(seconds=1),
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    assert second_open is True
    opened_state = _runtime_health_state(opened, now + timedelta(seconds=1))
    assert opened_state["runtime_failure_streak"] == 2

    preserved, still_open = _write_provider_runtime_failure(
        opened,
        kind="connection",
        error="late concurrent completion",
        occurred_at=now + timedelta(seconds=2),
        duration_seconds=1.0,
        cooldown_seconds=60,
    )
    preserved_state = _runtime_health_state(preserved, now + timedelta(seconds=2))

    assert still_open is True
    assert preserved_state["runtime_failure_streak"] == 2
    assert (
        preserved_state["runtime_failure_cooldown_until"]
        == opened_state["runtime_failure_cooldown_until"]
    )


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


def test_provider_check_rate_limited_only_when_no_sibling_succeeds() -> None:
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

    assert status == "rate_limited"
    assert message == "quota exhausted"
