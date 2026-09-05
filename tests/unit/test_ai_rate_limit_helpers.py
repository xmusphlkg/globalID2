from types import SimpleNamespace

from src.ai.model_center import (
    _effective_route_check_status,
    extract_retry_after_seconds,
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
