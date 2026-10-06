"""SMTP email delivery service using AWS SES or any SMTP provider."""

from __future__ import annotations

import smtplib
import ssl
import time
from email.utils import make_msgid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Iterable, Optional

from src.core import get_logger
from src.services.settings_service import system_settings_service

logger = get_logger(__name__)

_SMTP_MAX_ATTEMPTS = 3
_SMTP_RETRY_BASE_DELAY_SECONDS = 1.0
_SMTP_AUTH_FAILURE_COOLDOWN_SECONDS = 300.0
_smtp_auth_failure_retry_after: float = 0.0


def _is_retryable_smtp_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPNotSupportedError,
        ),
    ):
        return False
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= int(exc.smtp_code) < 500
    return isinstance(
        exc,
        (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            TimeoutError,
            OSError,
        ),
    )


def _is_auth_failure_error(exc: BaseException) -> bool:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        try:
            return int(exc.smtp_code) == 535
        except (TypeError, ValueError):
            return False
    return False


def _record_auth_failure_cooldown(now: float, *, message: str) -> None:
    global _smtp_auth_failure_retry_after
    _smtp_auth_failure_retry_after = now + _SMTP_AUTH_FAILURE_COOLDOWN_SECONDS
    logger.warning("SMTP auth-related failure; suppressing retries for {}s: {}", int(_SMTP_AUTH_FAILURE_COOLDOWN_SECONDS), message)


def _close_smtp_connection(server: smtplib.SMTP, *, delivered: bool) -> None:
    try:
        server.quit()
    except Exception as exc:
        if delivered:
            # Delivery has already succeeded. A dropped QUIT response must not
            # convert it into a failure and trigger a duplicate alert.
            logger.warning("SMTP email delivered but connection close failed: {}", exc)
        else:
            logger.debug("SMTP connection close failed after send error: {}", exc)


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
        raise_on_error: bool = False,
    ) -> bool:
        config = self._settings.smtp_runtime()
        recipient_list = [addr.strip() for addr in recipients if addr and addr.strip()]
        if not recipient_list:
            logger.warning("SMTP email skipped: no recipients configured")
            return False

        now = time.time()
        if _smtp_auth_failure_retry_after and now < _smtp_auth_failure_retry_after:
            logger.warning(
                "SMTP auth configuration issue active; skipping send for {} more second(s).",
                int(_smtp_auth_failure_retry_after - now),
            )
            return False

        from_email = config["smtp_from_email"]

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = from_email
            msg["To"] = ", ".join(recipient_list)
            msg["Subject"] = subject
            msg["Message-ID"] = make_msgid()

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

            message = msg.as_string()
            last_error: Exception | None = None
            for attempt in range(1, _SMTP_MAX_ATTEMPTS + 1):
                server = None
                delivered = False
                try:
                    server = self._create_connection()
                    server.sendmail(from_email, all_recipients, message)
                    delivered = True
                except Exception as exc:
                    last_error = exc
                finally:
                    if server is not None:
                        _close_smtp_connection(server, delivered=delivered)

                if delivered:
                    logger.info(f"Sent SMTP email to {len(recipient_list)} recipient(s)")
                    return True
                if (
                    last_error is None
                    or not _is_retryable_smtp_error(last_error)
                    or attempt >= _SMTP_MAX_ATTEMPTS
                ):
                    break
                delay = _SMTP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Transient SMTP failure; retrying attempt={}/{} delay_seconds={:.1f} error_type={}",
                    attempt + 1,
                    _SMTP_MAX_ATTEMPTS,
                    delay,
                    type(last_error).__name__,
                )
                time.sleep(delay)

            if last_error is not None:
                if _is_auth_failure_error(last_error):
                    _record_auth_failure_cooldown(time.time(), message=str(last_error))
                raise last_error
            raise RuntimeError("SMTP delivery failed without an error")
        except Exception as exc:
            logger.error(f"Failed to send SMTP email: {exc}")
            if raise_on_error:
                raise
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
