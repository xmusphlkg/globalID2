from datetime import datetime, timezone

import pytest

from src.ai.model_center import (
    availability_view,
    drop_rejected_optional_parameter,
    normalize_availability_windows,
    route_available_at,
    visible_assistant_text,
    with_availability,
)


def test_visible_answer_drops_reasoning_blocks_and_think_tags() -> None:
    text = visible_assistant_text(
        [
            {"type": "thinking", "text": "plan the json"},
            {"type": "text", "text": "<think>still planning</think>\n{\"status\":\"ok\"}"},
        ]
    )
    assert text == '{"status":"ok"}'
    assert visible_assistant_text("<thinking>draft</thinking>final answer") == "final answer"


def test_rejected_optional_parameter_is_removed_once() -> None:
    payload = {"model": "hy4", "messages": [], "temperature": 0, "extra_body": {"thinking": {"type": "enabled"}, "top_k": 1}}
    dropped: set[str] = set()
    error = Exception("Unsupported parameter: 'temperature'. Please remove it.")
    assert drop_rejected_optional_parameter(error, payload, dropped)
    assert "temperature" not in payload
    assert not drop_rejected_optional_parameter(error, payload, dropped)

    thinking_error = Exception("invalid parameter: thinking is not supported")
    assert drop_rejected_optional_parameter(thinking_error, payload, dropped)
    assert payload["extra_body"] == {"top_k": 1}
    assert not drop_rejected_optional_parameter(Exception("model not found"), payload, dropped)


def test_empty_schedule_is_always_available() -> None:
    assert route_available_at({}) is True
    assert route_available_at({"availability_windows": []}) is True
    cleaned = with_availability({"runtime_max_concurrency": 2, "availability_windows": [{"start": "00:00", "end": "08:00"}]}, "Asia/Shanghai", [])
    assert "availability_windows" not in cleaned
    assert cleaned["runtime_max_concurrency"] == 2


def test_night_window_uses_the_schedule_timezone() -> None:
    params = with_availability({}, "Asia/Shanghai", [{"start": "22:00:00", "end": "08:00"}])
    assert params["availability_windows"] == [{"start": "22:00", "end": "08:00"}]
    # 13:30 UTC is 21:30 in Shanghai, just before the window.
    before = datetime(2026, 10, 31, 13, 30, tzinfo=timezone.utc)
    # 14:00 UTC is 22:00 in Shanghai.
    opening = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)
    # 23:59 UTC is 07:59 the next morning in Shanghai.
    inside = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
    # 00:00 UTC is 08:00 in Shanghai, the window's exclusive end.
    closed = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    assert route_available_at(params, before) is False
    assert route_available_at(params, opening) is True
    assert route_available_at(params, inside) is True
    assert route_available_at(params, closed) is False


def test_daytime_window_and_invalid_schedule_fail_open() -> None:
    params = with_availability({}, None, [{"start": "09:00", "end": "17:00"}])
    assert availability_view(params)["availability_timezone"] == "Asia/Shanghai"
    morning = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)  # 09:00 Shanghai
    assert route_available_at(params, morning) is True
    assert route_available_at({"availability_windows": [{"start": "bad", "end": "nope"}]}, morning) is True
    with pytest.raises(ValueError):
        normalize_availability_windows([{"start": "08:00", "end": "08:00"}])
    with pytest.raises(ValueError):
        with_availability({}, "Not/AZone", [{"start": "00:00", "end": "01:00"}])
