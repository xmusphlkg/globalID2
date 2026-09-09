from __future__ import annotations

import pytest

import src.literature.pipeline as pipeline_module
from src.literature.pipeline import _is_postgres_deadlock, _retry_postgres_deadlock


class _DeadlockError(RuntimeError):
    sqlstate = "40P01"


def test_deadlock_detection_follows_wrapped_database_error() -> None:
    wrapped = RuntimeError("statement failed")
    wrapped.__cause__ = _DeadlockError("transaction chosen as victim")

    assert _is_postgres_deadlock(wrapped) is True
    assert _is_postgres_deadlock(RuntimeError("connection reset")) is False


@pytest.mark.asyncio
async def test_deadlock_retry_uses_fresh_operation_attempt(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _DeadlockError("deadlock")
        return "committed"

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(pipeline_module.asyncio, "sleep", fake_sleep)

    result = await _retry_postgres_deadlock(
        operation,
        max_retries=3,
        base_delay_seconds=0.25,
    )

    assert result == "committed"
    assert attempts == 3
    assert delays == [0.25, 0.5]


@pytest.mark.asyncio
async def test_non_deadlock_database_error_is_not_retried(monkeypatch) -> None:
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("connection reset")

    async def fail_sleep(_delay: float) -> None:
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(pipeline_module.asyncio, "sleep", fail_sleep)

    with pytest.raises(RuntimeError, match="connection reset"):
        await _retry_postgres_deadlock(
            operation,
            max_retries=3,
            base_delay_seconds=0.25,
        )
    assert attempts == 1
