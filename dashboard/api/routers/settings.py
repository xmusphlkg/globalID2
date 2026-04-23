"""Unified runtime settings router."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..schemas.settings import (
    CloudflareSettingsOut,
    CloudflareSettingsUpdate,
    GithubSettingsOut,
    GithubSettingsUpdate,
    RuntimeSettingsOut,
    SmtpSettingsOut,
    SmtpSettingsUpdate,
    TestEmailRequest,
)
from src.services.settings_service import system_settings_service
from src.services.smtp_email_service import smtp_email_service

router = APIRouter()


@router.get("/settings", response_model=RuntimeSettingsOut)
async def get_settings():
    snapshot = system_settings_service.public_snapshot()
    return RuntimeSettingsOut(
        smtp=SmtpSettingsOut(**snapshot["smtp"]),
        github=GithubSettingsOut(**snapshot["github"]),
        cloudflare=CloudflareSettingsOut(**snapshot["cloudflare"]),
    )


@router.put("/settings/smtp", response_model=RuntimeSettingsOut)
async def update_smtp_settings(body: SmtpSettingsUpdate):
    payload = body.model_dump(exclude_unset=True)
    snapshot = system_settings_service.update_smtp(payload)
    return RuntimeSettingsOut(
        smtp=SmtpSettingsOut(**snapshot["smtp"]),
        github=GithubSettingsOut(**snapshot["github"]),
        cloudflare=CloudflareSettingsOut(**snapshot["cloudflare"]),
    )


@router.put("/settings/github", response_model=RuntimeSettingsOut)
async def update_github_settings(body: GithubSettingsUpdate):
    payload = body.model_dump(exclude_unset=True)
    snapshot = system_settings_service.update_github(payload)
    return RuntimeSettingsOut(
        smtp=SmtpSettingsOut(**snapshot["smtp"]),
        github=GithubSettingsOut(**snapshot["github"]),
        cloudflare=CloudflareSettingsOut(**snapshot["cloudflare"]),
    )


@router.put("/settings/cloudflare", response_model=RuntimeSettingsOut)
async def update_cloudflare_settings(body: CloudflareSettingsUpdate):
    payload = body.model_dump(exclude_unset=True)
    snapshot = system_settings_service.update_cloudflare(payload)
    return RuntimeSettingsOut(
        smtp=SmtpSettingsOut(**snapshot["smtp"]),
        github=GithubSettingsOut(**snapshot["github"]),
        cloudflare=CloudflareSettingsOut(**snapshot["cloudflare"]),
    )


@router.delete("/settings/{section}", response_model=RuntimeSettingsOut)
async def reset_settings_section(section: str):
    normalized = section.strip().lower()
    if normalized not in {"smtp", "github", "cloudflare"}:
        raise HTTPException(404, f"Unknown settings section: {section}")
    snapshot = system_settings_service.reset_section(normalized)
    return RuntimeSettingsOut(
        smtp=SmtpSettingsOut(**snapshot["smtp"]),
        github=GithubSettingsOut(**snapshot["github"]),
        cloudflare=CloudflareSettingsOut(**snapshot["cloudflare"]),
    )


@router.post("/settings/smtp/test")
async def test_smtp_connection():
    if not smtp_email_service.is_configured():
        raise HTTPException(
            400,
            "SMTP is not configured. Set SMTP host, username, password, and from email in Settings.",
        )

    ok = smtp_email_service.test_connection()
    if not ok:
        raise HTTPException(
            500,
            "SMTP connection test failed. Check host, port, username, password, and TLS settings.",
        )

    return {
        "ok": True,
        "message": "SMTP connection successful",
        "checked_at": datetime.now().isoformat(),
    }


@router.post("/settings/smtp/send-test-email")
async def send_test_email(body: TestEmailRequest):
    recipient = body.recipient.strip()
    if not recipient:
        raise HTTPException(400, "Recipient email is required.")

    if not smtp_email_service.is_configured():
        raise HTTPException(
            400,
            "SMTP is not configured. Set SMTP host, username, password, and from email in Settings.",
        )

    sent = smtp_email_service.send_email(
        recipients=[recipient],
        subject="[GIDS] Test Email",
        body_html=(
            "<html><body>"
            "<h2>GIDS Test Email</h2>"
            "<p>This is a test email sent from the GIDS dashboard settings module.</p>"
            f"<p><strong>Recipient:</strong> {recipient}</p>"
            f"<p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>"
            "</body></html>"
        ),
    )
    if not sent:
        raise HTTPException(500, f"Failed to send test email to {recipient}")

    return {
        "ok": True,
        "message": f"Test email sent successfully to {recipient}",
        "checked_at": datetime.now().isoformat(),
    }

