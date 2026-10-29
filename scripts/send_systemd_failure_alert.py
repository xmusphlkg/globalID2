#!/usr/bin/env python3
"""Send rate-limited systemd failure alerts using the application's SMTP config."""

from __future__ import annotations

import fcntl
import os
import re
import smtplib
import ssl
import sys
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core import get_config, get_logger

logger = get_logger(__name__)
ALERT_COOLDOWN_SECONDS = 15 * 60
# Pauses between delivery attempts: three attempts in total.
RETRY_DELAYS_SECONDS = (2.0, 5.0)


def _state_dir() -> Path:
    runtime_dir = os.environ.get("RUNTIME_DIRECTORY")
    path = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir()) / "globalid-notify"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_transient(exc: Exception) -> bool:
    """Connection-level failures are worth retrying; auth and recipient errors are not."""
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return True
    # SMTPException subclasses OSError, so exclude it explicitly.
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


def _deliver_once(automation, message: EmailMessage, recipients: list[str]) -> bool:
    """One SMTP session. Returns True when at least one recipient accepted the message."""
    server = None
    delivered = False
    try:
        if automation.smtp_use_tls:
            server = smtplib.SMTP(automation.smtp_host, automation.smtp_port, timeout=20)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(
                automation.smtp_host,
                automation.smtp_port,
                timeout=20,
                context=ssl.create_default_context(),
            )
        server.login(automation.smtp_username, automation.smtp_password)
        refused = server.send_message(message)
        delivered = len(refused) < len(recipients)
        return delivered
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception as exc:
                if delivered:
                    logger.warning(
                        "SMTP accepted failure email, but connection close failed: {}",
                        type(exc).__name__,
                    )
                else:
                    server.close()


def send_failure_alert(service_name: str) -> bool:
    automation = get_config().automation
    recipients = automation.admin_emails
    if not recipients:
        logger.error("Failure alert not sent: AUTOMATION__ADMIN_EMAILS_RAW is empty")
        return False

    required = (
        automation.smtp_host,
        automation.smtp_port,
        automation.smtp_username,
        automation.smtp_password,
        automation.smtp_from_email,
    )
    if not all(required):
        logger.error("Failure alert not sent: SMTP configuration is incomplete")
        return False

    safe_name = re.sub(r"[^A-Za-z0-9_.@-]", "_", service_name)[:160]
    state_dir = _state_dir()
    lock_path = state_dir / f"{safe_name}.lock"
    sent_path = state_dir / f"{safe_name}.last-attempt"

    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        now = time.time()
        try:
            previous_send = float(sent_path.read_text().strip())
        except (OSError, ValueError):
            previous_send = 0.0
        if now - previous_send < ALERT_COOLDOWN_SECONDS:
            logger.info("Failure email for {} suppressed by the 15-minute cooldown", service_name)
            return True

        message = EmailMessage()
        message["From"] = automation.smtp_from_email
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"[GlobalID CRITICAL] {service_name} entered failed state"
        message.set_content(
            f"The systemd service {service_name} on {os.uname().nodename} entered a failed state.\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            "Inspect the service journal and restore the underlying dependency or process."
        )

        attempts = len(RETRY_DELAYS_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            try:
                if _deliver_once(automation, message, recipients):
                    break
                logger.error("SMTP server refused all administrators for {}", service_name)
                return False
            except Exception as exc:
                retry = attempt < attempts and _is_transient(exc)
                logger.error(
                    "Failure email delivery failed for {} (attempt {}/{}): {}{}",
                    service_name,
                    attempt,
                    attempts,
                    type(exc).__name__,
                    ", retrying" if retry else "",
                )
                if not retry:
                    return False
                time.sleep(RETRY_DELAYS_SECONDS[attempt - 1])

        # Only a delivered alert starts the cooldown; a failed send must not
        # suppress the next one.
        sent_path.write_text(f"{time.time():.3f}\n")

    logger.warning(
        "Failure email sent for {} to {} administrator(s)", service_name, len(recipients)
    )
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: send_systemd_failure_alert.py SERVICE", file=sys.stderr)
        return 2
    return 0 if send_failure_alert(sys.argv[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
