import json
import ssl
from unittest.mock import AsyncMock
from urllib import error as urlerror

import pytest
from fastapi import BackgroundTasks, HTTPException

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
async def test_worker_request_retries_transient_ssl_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(req, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urlerror.URLError(ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"))
        assert req.full_url == "https://worker.example/api/subscriptions/options"
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(
        subscriptions, "_worker_base_url", lambda: "https://worker.example"
    )
    monkeypatch.setattr(subscriptions, "_admin_token", lambda: "token")
    monkeypatch.setattr(subscriptions, "WORKER_NETWORK_ATTEMPTS", 2)
    monkeypatch.setattr(subscriptions.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(subscriptions.urlrequest, "urlopen", fake_urlopen)

    result = await subscriptions._worker_request(
        "/api/subscriptions/options", admin=False
    )

    assert result == {"ok": True}
    assert calls == 2


@pytest.fixture
def notification_channel(monkeypatch):
    channel = AsyncMock(return_value={"ok": True, "campaign": {"id": "campaign-1"}})
    monkeypatch.setattr(subscriptions, "_worker_request", channel)
    return channel


async def test_notification_translation_queues_normalized_languages_and_preserves_response(
    monkeypatch,
    notification_channel,
):
    route = {
        "model_id": 7,
        "model_key": "fake:translator",
        "model_name": "translator",
        "provider_key": "fake",
        "provider_name": "Fake provider",
    }
    translations = {
        "en": {"subject": "  Update  ", "markdown": "  # Update\n[link](/en/)  "},
        "ja": {"subject": "あ" * 205, "markdown": "い" * 50005},
    }
    run = AsyncMock(
        return_value={
            "text": "Result:\n```json\n"
            + json.dumps({"translations": translations})
            + "\n```",
            "model_route": route,
        }
    )
    monkeypatch.setattr(subscriptions, "run_worker_operation", run)
    send = AsyncMock()
    monkeypatch.setattr(
        subscriptions, "_process_notification_campaign_background", send
    )
    background = BackgroundTasks()
    result = await subscriptions.create_subscription_notification(
        subscriptions.NotificationCreateRequest(
            markdown="  # 更新\n\n2026-10-30: **12** [链接](/zh/).  ",
            source_locale="ZH_cn",
            target_locales=["en-US", "ja_JP", "EN", "unknown"],
            list_codes=["weekly"],
            batch_size=17,
            max_recipients=42,
        ),
        background,
    )

    run.assert_awaited_once_with(
        "notification_translation",
        {
            "subject": "更新",
            "markdown": "# 更新\n\n2026-10-30: **12** [链接](/zh/).",
            "source_locale": "zh",
            "target_locales": ["en", "ja"],
        },
    )
    sent = notification_channel.await_args.kwargs["payload"]
    assert notification_channel.await_args.args == ("/api/admin/notifications",)
    assert sent["target_locales"] == ["zh", "en", "ja"]
    assert sent["default_locale"] == sent["source_locale"] == "zh"
    assert sent["contents"]["zh"] == {"subject": "更新", "markdown": sent["markdown"]}
    assert sent["contents"]["en"] == {
        "subject": "Update",
        "markdown": "# Update\n[link](/en/)",
    }
    assert sent["contents"]["ja"] == {"subject": "あ" * 200, "markdown": "い" * 50000}
    assert sent["ai"] == {
        "status": "translated",
        "model_route": route,
        "locales": ["zh", "en", "ja"],
    }
    assert sent["list_codes"] == ["weekly"]
    assert sent["max_recipients"] == 42
    assert result == {
        "ok": True,
        "campaign": {"id": "campaign-1"},
        "send_started": True,
    }
    send.assert_not_awaited()
    await background()
    send.assert_awaited_once_with("campaign-1", 17)


async def test_source_locale_only_skips_model_queue(monkeypatch, notification_channel):
    run = AsyncMock(
        side_effect=AssertionError("Source-only notifications need no model")
    )
    monkeypatch.setattr(subscriptions, "run_worker_operation", run)
    background = BackgroundTasks()
    result = await subscriptions.create_subscription_notification(
        subscriptions.NotificationCreateRequest(
            markdown="# Original",
            subject="  Custom\nsubject  ",
            source_locale="EN_gb",
            target_locales=["en"],
            start_sending=False,
        ),
        background,
    )
    run.assert_not_awaited()
    sent = notification_channel.await_args.kwargs["payload"]
    assert sent["contents"] == {
        "en": {"subject": "Custom subject", "markdown": "# Original"}
    }
    assert sent["ai"] == {"status": "skipped", "reason": "source_locale_only"}
    assert result["send_started"] is False
    assert not background.tasks


async def test_default_notification_languages_are_all_preserved(
    monkeypatch, notification_channel
):
    translations = {
        locale: {"subject": locale, "markdown": f"# {locale}"}
        for locale in subscriptions.SUPPORTED_NOTIFICATION_LOCALES
        if locale != "zh"
    }
    run = AsyncMock(
        return_value={
            "text": json.dumps({"translations": translations}),
            "model_route": {},
        }
    )
    monkeypatch.setattr(subscriptions, "run_worker_operation", run)
    await subscriptions.create_subscription_notification(
        subscriptions.NotificationCreateRequest(
            markdown="# 更新", source_locale="unknown", start_sending=False
        ),
        BackgroundTasks(),
    )
    sent = notification_channel.await_args.kwargs["payload"]
    assert sent["source_locale"] == "zh"
    assert sent["target_locales"] == subscriptions.SUPPORTED_NOTIFICATION_LOCALES
    assert set(sent["contents"]) == set(subscriptions.SUPPORTED_NOTIFICATION_LOCALES)


@pytest.mark.parametrize(
    "text, error",
    [
        ("not JSON", "AI translation failed"),
        ("[]", "translations object"),
        ('{"translations": []}', "translations object"),
        ('{"translations": {}}', "missing locale: en"),
        (
            '{"translations": {"en": {"subject": " ", "markdown": "text"}}}',
            "empty content",
        ),
        (
            '{"translations": {"en": {"subject": "Update", "markdown": " "}}}',
            "empty content",
        ),
        (
            '{"translations": {"en": {"subject": "Update", "markdown": "text"}}}',
            "missing locale: ja",
        ),
    ],
)
async def test_invalid_translations_never_create_or_send_campaign(
    monkeypatch,
    notification_channel,
    text,
    error,
):
    monkeypatch.setattr(
        subscriptions,
        "run_worker_operation",
        AsyncMock(return_value={"text": text, "model_route": {}}),
    )
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await subscriptions.create_subscription_notification(
            subscriptions.NotificationCreateRequest(
                markdown="# 更新", target_locales=["en", "ja"]
            ),
            background,
        )
    assert exc.value.status_code == 502
    assert error in exc.value.detail
    notification_channel.assert_not_awaited()
    assert not background.tasks


async def test_failed_model_task_never_creates_or_sends_campaign(
    monkeypatch, notification_channel
):
    monkeypatch.setattr(
        subscriptions,
        "run_worker_operation",
        AsyncMock(side_effect=RuntimeError("Worker task failed")),
    )
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await subscriptions.create_subscription_notification(
            subscriptions.NotificationCreateRequest(
                markdown="# 更新", target_locales=["en"]
            ),
            background,
        )
    assert exc.value.status_code == 502
    assert "Worker task failed" in exc.value.detail
    notification_channel.assert_not_awaited()
    assert not background.tasks
