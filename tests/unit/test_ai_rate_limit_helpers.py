from types import SimpleNamespace

from src.ai.model_center import extract_retry_after_seconds, is_rate_limit_error


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


def test_extract_retry_after_seconds_reads_retry_after_header() -> None:
    error = DummyRateLimitError("Too many requests", status_code=429, headers={"retry-after": "17"})

    assert extract_retry_after_seconds(error) == 17


def test_extract_retry_after_seconds_parses_message_fallback() -> None:
    error = DummyRateLimitError("Rate limit reached, please try again in 9s.")

    assert extract_retry_after_seconds(error) == 9