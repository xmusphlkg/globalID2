from types import SimpleNamespace

from src.ai.model_center import (
    _effective_route_check_status,
    _health_check_failure_status,
    _legacy_transient_probe_unavailable,
    _runtime_failure_kind,
    extract_retry_after_seconds,
    is_model_channel_failure,
    is_provider_authentication_error,
    is_model_unavailable_error,
    is_rate_limit_error,
)


class DummyRateLimitError(Exception):
    def __init__(self, message: str, status_code: int | None = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {})


def test_is_rate_limit_error_detects_http_429() -> None:
    error = DummyRateLimitError("Too many requests", status_code=429)

    assert is_rate_limit_error(error) is True


def test_is_rate_limit_error_detects_quota_text() -> None:
    error = DummyRateLimitError("insufficient_quota: please upgrade your plan")

    assert is_rate_limit_error(error) is True


def test_is_rate_limit_error_detects_provider_code_2003() -> None:
    error = DummyRateLimitError(
        "Error code: 400 - {'error': {'message': '请求限频，请稍后重试', 'code': '2003'}}",
        status_code=400,
    )

    assert is_rate_limit_error(error) is True


def test_is_provider_authentication_error_detects_http_401_invalid_token() -> None:
    error = DummyRateLimitError(
        "Error code: 401 - {'error': {'message': 'Invalid token'}}",
        status_code=401,
    )

    assert is_provider_authentication_error(error) is True


def test_is_provider_authentication_error_detects_invalid_token_message() -> None:
    error = DummyRateLimitError("Authentication failed: invalid token")

    assert is_provider_authentication_error(error) is True


def test_is_provider_authentication_error_does_not_treat_model_acl_as_credentials() -> None:
    error = DummyRateLimitError(
        "Error code: 403 - {'error': {'message': '无权访问 gemini特惠 分组'}}",
        status_code=403,
    )

    assert is_provider_authentication_error(error) is False


def test_is_provider_authentication_error_does_not_treat_rate_limit_as_credentials() -> None:
    error = DummyRateLimitError("Too many requests", status_code=429)

    assert is_provider_authentication_error(error) is False


def test_model_unavailable_error_does_not_mask_provider_authentication_failure() -> None:
    error = DummyRateLimitError("permission denied: invalid token", status_code=401)

    assert is_model_unavailable_error(error) is False


def test_provider_unavailable_dominates_older_model_available_status() -> None:
    assert _effective_route_check_status("available", "unavailable") == "unavailable"


def test_extract_retry_after_seconds_reads_retry_after_header() -> None:
    error = DummyRateLimitError("Too many requests", status_code=429, headers={"retry-after": "17"})

    assert extract_retry_after_seconds(error) == 17


def test_extract_retry_after_seconds_parses_message_fallback() -> None:
    error = DummyRateLimitError("Rate limit reached, please try again in 9s.")

    assert extract_retry_after_seconds(error) == 9


def test_extract_retry_after_seconds_parses_chinese_message() -> None:
    error = DummyRateLimitError("请求限频，请在 12 秒后重试")

    assert extract_retry_after_seconds(error) == 12


def test_extract_retry_after_seconds_parses_pool_resume_message() -> None:
    error = DummyRateLimitError(
        "Error code: 429 - every usable account in realm 'cn' is cooling the upstream hy3 window; "
        "the pool resumes in 6050s"
    )

    assert extract_retry_after_seconds(error) == 6050


def test_is_model_unavailable_error_detects_model_not_found_code() -> None:
    error = DummyRateLimitError(
        "Error code: 404 - {'error': {'message': 'The model `hunyuan-standard` does not exist or you do not have access to it.', 'code': 'model_not_found'}}",
        status_code=404,
    )

    assert is_model_unavailable_error(error) is True


def test_is_model_unavailable_error_detects_invalid_model_message() -> None:
    error = DummyRateLimitError("Invalid model: qwen-foo-bar")

    assert is_model_unavailable_error(error) is True


def test_is_model_unavailable_error_detects_chinese_no_access_message() -> None:
    error = DummyRateLimitError(
        "Error code: 403 - {'error': {'message': '无权访问 gemini特惠 分组'}}",
        status_code=403,
    )

    assert is_model_unavailable_error(error) is True


def test_is_model_unavailable_error_does_not_match_quota_error() -> None:
    error = DummyRateLimitError("insufficient_quota: please upgrade your plan")

    assert is_model_unavailable_error(error) is False


def test_is_model_unavailable_error_does_not_mask_channel_failure() -> None:
    error = DummyRateLimitError(
        "Error code: 500 - {'error': {'message': '分组 Claude特惠 下模型 claude-opus 的可用渠道不存在（retry）', 'code': 'get_channel_failed'}}",
        status_code=500,
    )

    assert is_model_unavailable_error(error) is False


def test_new_api_503_missing_channel_is_transient() -> None:
    error = DummyRateLimitError(
        "Error code: 503 - {'error': {'code': 'model_not_found', "
        "'message': 'No available channel for model hy3 under group default (distributor)', "
        "'type': 'new_api_error'}}",
        status_code=503,
    )

    assert is_model_channel_failure(error) is True
    assert is_model_unavailable_error(error) is False
    assert _health_check_failure_status(error) == "probe_failed"
    assert _runtime_failure_kind(error) == "upstream"
    assert _legacy_transient_probe_unavailable(str(error)) is True


def test_is_model_channel_failure_detects_get_channel_failed_code() -> None:
    error = DummyRateLimitError(
        "Error code: 500 - {'error': {'message': '分组 Claude特惠 下模型 claude-opus 的可用渠道不存在（retry）', 'code': 'get_channel_failed'}}",
        status_code=500,
    )

    assert is_model_channel_failure(error) is True


def test_is_model_channel_failure_detects_missing_channel_message() -> None:
    error = DummyRateLimitError(
        "Error code: 500 - {'error': {'message': 'No available channel for requested model right now. Please retry later.', 'code': ''}}",
        status_code=500,
    )

    assert is_model_channel_failure(error) is True


def test_rate_limit_cooldown_escalates_from_30s_to_ceiling() -> None:
    from src.ai.model_center import _escalating_rate_limit_cooldown

    assert [_escalating_rate_limit_cooldown(streak, 300) for streak in range(6)] == [30, 60, 120, 240, 300, 300]
    assert _escalating_rate_limit_cooldown(0, 10) == 10


def test_rate_limit_streak_resets_after_success_and_window() -> None:
    from datetime import timedelta

    from src.ai.model_center import (
        _clear_payload_rate_limit,
        _mark_payload_rate_limited,
        _rate_limit_streak,
        _utcnow,
    )

    now = _utcnow()
    payload = _mark_payload_rate_limited({}, now + timedelta(seconds=30), now)
    payload = _mark_payload_rate_limited(payload, now + timedelta(seconds=60), now)
    assert _rate_limit_streak(payload, now) == 2
    assert _rate_limit_streak(payload, now + timedelta(seconds=601)) == 0
    assert _rate_limit_streak(_clear_payload_rate_limit(payload, now), now) == 0


def test_rate_limit_cooldown_escalates_per_consecutive_limit_and_resets() -> None:
    from datetime import timedelta

    from src.ai.model_center import (
        _clear_payload_rate_limit,
        _escalating_rate_limit_cooldown,
        _mark_payload_rate_limited,
        _rate_limit_streak,
        _utcnow,
    )

    now = _utcnow()
    payload: dict = {}
    seen = []
    for _ in range(5):
        streak = _rate_limit_streak(payload, now)
        seen.append(_escalating_rate_limit_cooldown(streak, 300))
        payload = _mark_payload_rate_limited(payload, now, now)
    assert seen == [30, 60, 120, 240, 300]

    # A success clears the streak; so does a quiet period past the window.
    assert _rate_limit_streak(_clear_payload_rate_limit(payload, now), now) == 0
    assert _rate_limit_streak(payload, now + timedelta(seconds=601)) == 0
