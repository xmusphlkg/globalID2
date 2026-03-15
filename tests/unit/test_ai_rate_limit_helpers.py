from types import SimpleNamespace

from src.ai.model_center import (
    extract_retry_after_seconds,
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


def test_is_model_unavailable_error_does_not_match_quota_error() -> None:
    error = DummyRateLimitError("insufficient_quota: please upgrade your plan")

    assert is_model_unavailable_error(error) is False