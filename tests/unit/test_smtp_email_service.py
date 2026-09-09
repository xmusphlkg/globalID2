from __future__ import annotations

import smtplib
from types import SimpleNamespace

import pytest

import src.services.smtp_email_service as smtp_module
from src.services.smtp_email_service import SMTPEmailService


def _service() -> SMTPEmailService:
    service = SMTPEmailService()
    service._settings = SimpleNamespace(
        smtp_runtime=lambda: {
            "smtp_host": "smtp.example.test",
            "smtp_port": 587,
            "smtp_username": "user",
            "smtp_password": "secret",
            "smtp_from_email": "alerts@example.test",
            "smtp_use_tls": True,
        }
    )
    return service


class _Connection:
    def __init__(self, *, send_error=None, quit_error=None) -> None:
        self.send_error = send_error
        self.quit_error = quit_error
        self.messages: list[str] = []
        self.quit_calls = 0

    def sendmail(self, _sender, _recipients, message: str):
        self.messages.append(message)
        if self.send_error is not None:
            raise self.send_error
        return {}

    def quit(self) -> None:
        self.quit_calls += 1
        if self.quit_error is not None:
            raise self.quit_error


def test_transient_smtp_disconnect_retries_with_same_message_id(monkeypatch) -> None:
    service = _service()
    first_connection = _Connection(
        send_error=smtplib.SMTPServerDisconnected("closed")
    )
    second_connection = _Connection()
    connections = [first_connection, second_connection]
    delays: list[float] = []
    monkeypatch.setattr(service, "_create_connection", lambda: connections.pop(0))
    monkeypatch.setattr(smtp_module.time, "sleep", delays.append)

    sent = service.send_email(
        recipients=["ops@example.test"],
        subject="Alert",
        body_html="<p>failed task</p>",
    )

    assert sent is True
    assert delays == [1.0]
    first_message_id = next(
        line for line in first_connection.messages[0].splitlines() if line.startswith("Message-ID:")
    )
    second_message_id = next(
        line for line in second_connection.messages[0].splitlines() if line.startswith("Message-ID:")
    )
    assert first_message_id == second_message_id


def test_smtp_authentication_failure_is_not_retried(monkeypatch) -> None:
    service = _service()
    connection = _Connection(
        send_error=smtplib.SMTPAuthenticationError(535, b"bad credentials")
    )
    attempts = 0

    def create_connection():
        nonlocal attempts
        attempts += 1
        return connection

    monkeypatch.setattr(service, "_create_connection", create_connection)
    monkeypatch.setattr(
        smtp_module.time,
        "sleep",
        lambda _delay: pytest.fail("permanent failure should not sleep"),
    )

    assert service.send_email(
        recipients=["ops@example.test"],
        subject="Alert",
        body_html="<p>failed task</p>",
    ) is False
    assert attempts == 1


def test_quit_failure_after_delivery_does_not_report_send_failure(monkeypatch) -> None:
    service = _service()
    connection = _Connection(
        quit_error=smtplib.SMTPServerDisconnected("connection already closed")
    )
    monkeypatch.setattr(service, "_create_connection", lambda: connection)

    assert service.send_email(
        recipients=["ops@example.test"],
        subject="Alert",
        body_html="<p>failed task</p>",
    ) is True
    assert len(connection.messages) == 1
    assert connection.quit_calls == 1
