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


def _state_dir() -> Path:
    runtime_dir = os.environ.get("RUNTIME_DIRECTORY")
    path = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir()) / "globalid-notify"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
            previous_attempt = float(sent_path.read_text().strip())
        except (OSError, ValueError):
            previous_attempt = 0.0
        if now - previous_attempt < ALERT_COOLDOWN_SECONDS:
            logger.info("Failure email for {} suppressed by the 15-minute cooldown", service_name)
            return True

        # Reserve the window before network I/O so rapid restart loops cannot
        # flood either the SMTP provider or the admin inbox when SMTP is down.
        sent_path.write_text(f"{now:.3f}\n")

        message = EmailMessage()
        message["From"] = automation.smtp_from_email
        message["To"] = ", ".join(recipients)
        message["Subject"] = f"[GlobalID CRITICAL] {service_name} entered failed state"
        message.set_content(
            f"The systemd service {service_name} on {os.uname().nodename} entered a failed state.\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            "Inspect the service journal and restore the underlying dependency or process."
        )

        server = None
        delivered = False
        try:
            if automation.smtp_use_tls:
                server = smtplib.SMTP(
                    automation.smtp_host, automation.smtp_port, timeout=20
                )
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
            if not delivered:
                logger.error("SMTP server refused all administrators for {}", service_name)
                return False
        except Exception as exc:
            logger.error("Failure email delivery failed for {}: {}", service_name, type(exc).__name__)
            return False
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception as exc:
                    if delivered:
                        logger.warning(
                            "SMTP accepted failure email for {}, but connection close failed: {}",
                            service_name,
                            type(exc).__name__,
                        )
                    else:
                        server.close()

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
