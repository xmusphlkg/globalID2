import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.generation.email_service import EmailService


def test_send_report_to_settings_recipients_requires_smtp(monkeypatch):
    monkeypatch.setattr(
        "src.generation.email_service.system_settings_service.build_smtp_status",
        lambda: {
            "admin_emails": ["ops@example.com"],
            "smtp_configured": False,
            "alerting_ready": False,
        },
    )

    delivery = EmailService().send_report_to_settings_recipients(
        report_title="Monthly Report",
        report_html="<p>hello</p>",
    )

    assert delivery["sent"] is False
    assert delivery["reason"] == "smtp_not_configured"
    assert delivery["recipients"] == ["ops@example.com"]


def test_send_report_to_settings_recipients_requires_recipients(monkeypatch):
    monkeypatch.setattr(
        "src.generation.email_service.system_settings_service.build_smtp_status",
        lambda: {
            "admin_emails": [],
            "smtp_configured": True,
            "alerting_ready": False,
        },
    )

    delivery = EmailService().send_report_to_settings_recipients(
        report_title="Monthly Report",
        report_html="<p>hello</p>",
    )

    assert delivery["sent"] is False
    assert delivery["reason"] == "missing_recipients"


def test_send_report_to_settings_recipients_uses_shared_recipients(monkeypatch):
    monkeypatch.setattr(
        "src.generation.email_service.system_settings_service.build_smtp_status",
        lambda: {
            "admin_emails": ["ops@example.com", "dev@example.com"],
            "smtp_configured": True,
            "alerting_ready": True,
        },
    )

    called: dict[str, object] = {}

    def _fake_send_report(self, to_addrs, report_title, report_html, pdf_path=None, **kwargs):
        called["to_addrs"] = list(to_addrs)
        called["report_title"] = report_title
        called["report_html"] = report_html
        called["pdf_path"] = pdf_path
        return True

    monkeypatch.setattr(EmailService, "send_report", _fake_send_report)

    delivery = EmailService().send_report_to_settings_recipients(
        report_title="Monthly Report",
        report_html="<p>hello</p>",
        pdf_path="/tmp/report.pdf",
    )

    assert delivery["sent"] is True
    assert delivery["reason"] is None
    assert called["to_addrs"] == ["ops@example.com", "dev@example.com"]
    assert called["report_title"] == "Monthly Report"
    assert called["pdf_path"] == "/tmp/report.pdf"

