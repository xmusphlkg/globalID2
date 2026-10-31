import pytest

from src.ai.model_center import (
    get_thinking_mode,
    route_completion_extra_body,
    with_thinking_mode,
)


def test_no_provider_gets_implicit_request_options() -> None:
    route = {
        "provider_key": "tencent_plan",
        "model_name": "hy3",
        "base_url": "https://api.lkeap.cloud.tencent.com/plan/v3",
        "extra_params": {},
    }
    assert route_completion_extra_body(route) is None


def test_explicit_extra_body_is_returned_as_a_copy() -> None:
    body = {"thinking": {"type": "disabled"}}
    route = {"provider_key": "ctm", "extra_params": {"extra_body": body}}

    result = route_completion_extra_body(route)

    assert result == body
    assert result is not body


@pytest.mark.parametrize("extra_params", [None, "bad", {}, {"extra_body": None}, {"extra_body": {}}, {"extra_body": "x"}])
def test_missing_or_malformed_extra_body_yields_none(extra_params) -> None:
    assert route_completion_extra_body({"extra_params": extra_params}) is None


@pytest.mark.parametrize(
    ("extra_params", "expected"),
    [
        (None, "default"),
        ({}, "default"),
        ({"extra_body": {"thinking": {"type": "enabled"}}}, "enabled"),
        ({"extra_body": {"thinking": {"type": "disabled"}}}, "disabled"),
        ({"extra_body": {"thinking": {"type": "auto"}}}, "default"),
        ({"extra_body": {"thinking": "disabled"}}, "default"),
        ({"extra_body": {"other": 1}}, "default"),
    ],
)
def test_get_thinking_mode(extra_params, expected) -> None:
    assert get_thinking_mode(extra_params) == expected


def test_with_thinking_mode_sets_and_clears_without_touching_siblings() -> None:
    original = {"runtime_max_concurrency": 4, "extra_body": {"top_k": 20}}

    disabled = with_thinking_mode(original, "disabled")
    assert disabled["extra_body"] == {"top_k": 20, "thinking": {"type": "disabled"}}
    assert disabled["runtime_max_concurrency"] == 4
    assert original == {"runtime_max_concurrency": 4, "extra_body": {"top_k": 20}}

    cleared = with_thinking_mode(disabled, "default")
    assert cleared == original


def test_with_thinking_mode_default_removes_empty_extra_body() -> None:
    params = {"extra_body": {"thinking": {"type": "enabled"}}, "runtime_max_concurrency": 2}
    assert with_thinking_mode(params, "default") == {"runtime_max_concurrency": 2}


def test_reapplying_current_mode_preserves_provider_specific_values() -> None:
    params = {"extra_body": {"thinking": {"type": "disabled", "budget_tokens": 0}}}
    assert with_thinking_mode(params, "disabled") == params


def test_with_thinking_mode_handles_non_dict_input() -> None:
    assert with_thinking_mode(None, "enabled") == {"extra_body": {"thinking": {"type": "enabled"}}}
    assert with_thinking_mode(None, "default") == {}


def test_with_thinking_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        with_thinking_mode({}, "auto")


@pytest.mark.parametrize(
    "route",
    [
        {"extra_params": {"runtime_completion_max_tokens": 8000}},
        {"extra_params": {"runtime_completion_max_tokens": 99999}},
        {"extra_params": {"extra_body": {"thinking": {"type": "disabled"}}}},
        {"extra_params": {}},
        {"extra_params": {"extra_body": {"thinking": {"type": "enabled"}}}},
        {"extra_params": {}, "max_tokens": 1000},
    ],
)
def test_completion_requests_are_not_capped(route) -> None:
    from src.ai.model_center import runtime_route_completion_max_tokens

    assert runtime_route_completion_max_tokens(route, 2400) is None


def test_completion_sanitizer_strips_probe_only_settings_but_keeps_extra_body() -> None:
    from src.ai.agents.base import BaseAgent

    sanitized = BaseAgent._sanitize_completion_params(
        {
            "health_check_timeout_seconds": 90,
            "health_check_max_tokens": 4096,
            "runtime_completion_max_tokens": 12000,
            "availability_windows": [{"start": "00:00", "end": "08:00"}],
            "availability_timezone": "Asia/Shanghai",
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    )
    assert sanitized == {"extra_body": {"thinking": {"type": "disabled"}}}
