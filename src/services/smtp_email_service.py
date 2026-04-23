"""SMTP email delivery service using AWS SES or any SMTP provider."""

from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Iterable, Optional

from src.core import get_logger
from src.services.settings_service import system_settings_service

logger = get_logger(__name__)


class SMTPEmailService:
    """Send email via SMTP (AWS SES, SendGrid, etc.)."""

    def __init__(self) -> None:
        self._settings = system_settings_service

    def is_configured(self) -> bool:
        config = self._settings.smtp_runtime()
        return bool(
            config["smtp_host"]
            and config["smtp_port"]
            and config["smtp_username"]
            and config["smtp_password"]
            and config["smtp_from_email"]
        )

    def _create_connection(self) -> smtplib.SMTP:
        config = self._settings.smtp_runtime()
        context = ssl.create_default_context()

        if config["smtp_use_tls"]:
            server = smtplib.SMTP(config["smtp_host"], config["smtp_port"])
            server.starttls(context=context)
        else:
            server = smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=context)

        server.login(config["smtp_username"], config["smtp_password"])
        return server

    def send_email(
        self,
        *,
        recipients: Iterable[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachments: Optional[Iterable[str]] = None,
        cc_recipients: Optional[Iterable[str]] = None,
        bcc_recipients: Optional[Iterable[str]] = None,
    ) -> bool:
        config = self._settings.smtp_runtime()
        recipient_list = [addr.strip() for addr in recipients if addr and addr.strip()]
        if not recipient_list:
            logger.warning("SMTP email skipped: no recipients configured")
            return False

        from_email = config["smtp_from_email"]

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = from_email
            msg["To"] = ", ".join(recipient_list)
            msg["Subject"] = subject

            cc_list = [addr.strip() for addr in (cc_recipients or []) if addr and addr.strip()]
            bcc_list = [addr.strip() for addr in (bcc_recipients or []) if addr and addr.strip()]

            if cc_list:
                msg["Cc"] = ", ".join(cc_list)

            # Add HTML body with optional text fallback
            alt_part = MIMEMultipart("alternative")
            if body_text:
                alt_part.attach(MIMEText(body_text, "plain", "utf-8"))
            if body_html:
                alt_part.attach(MIMEText(body_html, "html", "utf-8"))
            elif body_text:
                alt_part.attach(MIMEText(body_text, "html", "utf-8"))
            msg.attach(alt_part)

            # Add attachments
            for attachment_path in (attachments or []):
                attachment_path_obj = Path(attachment_path)
                if not attachment_path_obj.exists():
                    logger.warning(f"Attachment not found: {attachment_path_obj}")
                    continue
                try:
                    with open(attachment_path_obj, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename={attachment_path_obj.name}",
                        )
                        msg.attach(part)
                except Exception as exc:
                    logger.error(f"Failed to attach {attachment_path_obj}: {exc}")

            # Collect all recipients (including BCC for actual delivery)
            all_recipients = list(recipient_list)
            if cc_list:
                all_recipients.extend(cc_list)
            if bcc_list:
                all_recipients.extend(bcc_list)

            server = self._create_connection()
            try:
                server.sendmail(from_email, all_recipients, msg.as_string())
                logger.info(f"Sent SMTP email to {len(recipient_list)} recipient(s)")
                return True
            finally:
                server.quit()
        except Exception as exc:
            logger.error(f"Failed to send SMTP email: {exc}")
            return False

    def test_connection(self) -> bool:
        try:
            server = self._create_connection()
            try:
                server.noop()
                logger.info("SMTP email connection successful")
                return True
            finally:
                server.quit()
        except Exception as exc:
            logger.error(f"SMTP email connection failed: {exc}")
            return False


smtp_email_service = SMTPEmailService()
