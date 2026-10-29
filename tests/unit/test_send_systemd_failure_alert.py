from __future__ import annotations

import importlib.util
import smtplib
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "send_systemd_failure_alert.py"


@pytest.fixture()
def alert(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("send_systemd_failure_alert_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    automation = SimpleNamespace(
        admin_emails=["ops@example.test"],
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        smtp_from_email="alerts@example.test",
        smtp_use_tls=True,
    )
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(automation=automation))
    monkeypatch.setattr(module, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    module.state_file = tmp_path / "svc.service.last-attempt"
    return module


def _script_deliveries(monkeypatch, alert, outcomes):
    calls = []

    def fake(_automation, _message, _recipients):
        calls.append(1)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(alert, "_deliver_once", fake)
    return calls


def test_transient_disconnect_is_retried_and_cooldown_starts_after_success(alert, monkeypatch):
    calls = _script_deliveries(
        monkeypatch, alert, [smtplib.SMTPServerDisconnected("x"), smtplib.SMTPServerDisconnected("x"), True]
    )

    assert alert.send_failure_alert("svc.service") is True
    assert len(calls) == 3
    assert alert.state_file.exists()


def test_failed_send_does_not_start_cooldown(alert, monkeypatch):
    calls = _script_deliveries(monkeypatch, alert, [smtplib.SMTPServerDisconnected("x")])

    assert alert.send_failure_alert("svc.service") is False
    assert len(calls) == len(alert.RETRY_DELAYS_SECONDS) + 1
    assert not alert.state_file.exists()

    _script_deliveries(monkeypatch, alert, [True])
    assert alert.send_failure_alert("svc.service") is True


def test_authentication_error_is_not_retried(alert, monkeypatch):
    calls = _script_deliveries(monkeypatch, alert, [smtplib.SMTPAuthenticationError(535, b"bad")])

    assert alert.send_failure_alert("svc.service") is False
    assert len(calls) == 1
    assert not alert.state_file.exists()


def test_all_recipients_refused_is_not_retried(alert, monkeypatch):
    calls = _script_deliveries(monkeypatch, alert, [False])

    assert alert.send_failure_alert("svc.service") is False
    assert len(calls) == 1
    assert not alert.state_file.exists()


def test_successful_send_suppresses_the_next_alert_within_cooldown(alert, monkeypatch):
    calls = _script_deliveries(monkeypatch, alert, [True])

    assert alert.send_failure_alert("svc.service") is True
    assert alert.send_failure_alert("svc.service") is True
    assert len(calls) == 1
