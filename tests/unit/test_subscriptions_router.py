import ssl
from urllib import error as urlerror

import pytest

from dashboard.api.routers import subscriptions


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


@pytest.mark.asyncio
async def test_worker_request_retries_transient_ssl_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_urlopen(req, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urlerror.URLError(ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"))
        assert req.full_url == "https://worker.example/api/subscriptions/options"
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(subscriptions, "_worker_base_url", lambda: "https://worker.example")
    monkeypatch.setattr(subscriptions, "_admin_token", lambda: "token")
    monkeypatch.setattr(subscriptions, "WORKER_NETWORK_ATTEMPTS", 2)
    monkeypatch.setattr(subscriptions.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(subscriptions.urlrequest, "urlopen", fake_urlopen)

    result = await subscriptions._worker_request("/api/subscriptions/options", admin=False)

    assert result == {"ok": True}
    assert calls == 2
