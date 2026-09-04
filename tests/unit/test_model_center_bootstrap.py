from __future__ import annotations

import asyncio

import pytest

from src.ai import model_center


@pytest.fixture(autouse=True)
def _reset_bootstrap_cache():
    model_center.invalidate_model_center_bootstrap_cache()
    yield
    model_center.invalidate_model_center_bootstrap_cache()


def _install_bootstrap_fakes(monkeypatch, *, engine=None):
    current_engine = {"value": engine or object()}
    calls: list[bool] = []
    schema_calls = 0

    async def ensure_tables() -> None:
        nonlocal schema_calls
        schema_calls += 1

    async def bootstrap_uncached(force: bool = False) -> None:
        calls.append(force)
        # Give concurrent callers a chance to contend on the single-flight
        # lock. Only the lock owner may reach this function.
        await asyncio.sleep(0)

    monkeypatch.setattr(
        model_center, "get_engine", lambda: current_engine["value"]
    )
    monkeypatch.setattr(model_center, "ensure_model_center_tables", ensure_tables)
    monkeypatch.setattr(
        model_center,
        "_bootstrap_model_center_from_env_uncached",
        bootstrap_uncached,
    )
    return current_engine, calls, lambda: schema_calls


def test_structured_workload_probe_accepts_the_contract_payload() -> None:
    model_center._validate_structured_test_response(
        '{"status":"globalid-structured-probe-ok","items":[{"id":1,"summary":"ok"}]}'
    )


def test_model_catalogue_extraction_is_stable_and_deduplicated() -> None:
    payload = {"data": [{"id": "qwen3.8-flash"}, {"id": "qwen3.6-flash"}, {"id": "qwen3.8-flash"}, {"missing": "id"}]}

    assert model_center._model_ids_from_catalogue(payload) == ["qwen3.6-flash", "qwen3.8-flash"]


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"status":"unexpected","items":[{}]}',
        '{"status":"globalid-structured-probe-ok","items":[]}',
    ],
)
def test_structured_workload_probe_rejects_invalid_payloads(response: str) -> None:
    with pytest.raises(RuntimeError):
        model_center._validate_structured_test_response(response)


@pytest.mark.asyncio
async def test_initial_empty_bootstrap_is_single_flight_and_fresh_calls_skip_db(
    monkeypatch,
) -> None:
    _, calls, schema_calls = _install_bootstrap_fakes(monkeypatch)

    await asyncio.gather(
        *(model_center.bootstrap_model_center_from_env() for _ in range(20))
    )
    await model_center.bootstrap_model_center_from_env()

    assert calls == [False]
    assert schema_calls() == 1


@pytest.mark.asyncio
async def test_force_always_bypasses_bootstrap_cache(monkeypatch) -> None:
    _, calls, _ = _install_bootstrap_fakes(monkeypatch)

    await model_center.bootstrap_model_center_from_env()
    await model_center.bootstrap_model_center_from_env(force=True)
    await model_center.bootstrap_model_center_from_env(force=True)

    assert calls == [False, True, True]


@pytest.mark.asyncio
async def test_changed_engine_does_not_reuse_previous_database_check(monkeypatch) -> None:
    current_engine, calls, _ = _install_bootstrap_fakes(monkeypatch)

    await model_center.bootstrap_model_center_from_env()
    current_engine["value"] = object()
    await model_center.bootstrap_model_center_from_env()

    assert calls == [False, False]


@pytest.mark.asyncio
async def test_expired_check_revisits_database_after_out_of_band_clear(
    monkeypatch,
) -> None:
    _, calls, _ = _install_bootstrap_fakes(monkeypatch)
    monotonic_now = {"value": 100.0}
    monkeypatch.setattr(
        model_center.time, "monotonic", lambda: monotonic_now["value"]
    )

    await model_center.bootstrap_model_center_from_env()
    # This represents the same database being cleared by an external
    # maintenance process. The bounded cache must eventually run the normal
    # empty-database bootstrap path again without requiring force=True.
    monotonic_now["value"] += model_center._BOOTSTRAP_RECHECK_SECONDS + 1
    await model_center.bootstrap_model_center_from_env()

    assert calls == [False, False]


@pytest.mark.asyncio
async def test_failed_bootstrap_is_not_cached(monkeypatch) -> None:
    engine = object()
    attempts = 0

    async def ensure_tables() -> None:
        return None

    async def flaky_bootstrap(force: bool = False) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary database failure")

    monkeypatch.setattr(model_center, "get_engine", lambda: engine)
    monkeypatch.setattr(model_center, "ensure_model_center_tables", ensure_tables)
    monkeypatch.setattr(
        model_center,
        "_bootstrap_model_center_from_env_uncached",
        flaky_bootstrap,
    )

    with pytest.raises(RuntimeError, match="temporary database failure"):
        await model_center.bootstrap_model_center_from_env()
    await model_center.bootstrap_model_center_from_env()

    assert attempts == 2
