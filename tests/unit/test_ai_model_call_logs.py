from uuid import uuid4

import pytest

from src.ai import model_center
from src.domain.ai_model_center import AIModelCallLog


@pytest.mark.asyncio
async def test_model_call_log_persists_sanitized_metadata_only(monkeypatch):
    captured = []

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, row):
            captured.append(row)

    monkeypatch.setattr(model_center, "get_db", lambda: FakeDB())

    await model_center.record_model_call_log(
        request_id=uuid4(),
        route={
            "model_id": 3,
            "provider_id": 4,
            "model_key": "tencent:hy3",
            "model_name": "hy3",
            "provider_key": "tencent",
            "api_key": "sk-sensitive-provider-key",
        },
        model_name="hy3",
        provider_key="tencent",
        agent_name="test-agent",
        status="failed",
        latency_ms=125.678,
        error=RuntimeError("Authorization: Bearer sk-sensitive-token"),
    )

    assert len(captured) == 1
    row = captured[0]
    assert isinstance(row, AIModelCallLog)
    assert row.provider_key == "tencent"
    assert row.model_key == "tencent:hy3"
    assert row.status == "failed"
    assert row.latency_ms == 125.7
    assert "[REDACTED]" in row.error_message
    assert "sk-sensitive" not in row.error_message
    assert not hasattr(row, "api_key")
    assert not hasattr(row, "prompt")
    assert not hasattr(row, "response")


@pytest.mark.asyncio
async def test_empty_timeout_message_retains_the_exception_type(monkeypatch):
    captured = []

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, row):
            captured.append(row)

    monkeypatch.setattr(model_center, "get_db", lambda: FakeDB())
    await model_center.record_model_call_log(
        request_id=uuid4(), route=None, model_name="slow", provider_key="provider",
        agent_name="test-agent", status="failed", latency_ms=35000,
        error=TimeoutError(),
    )
    assert captured[0].error_type == "TimeoutError"
    assert captured[0].error_message == "TimeoutError"
