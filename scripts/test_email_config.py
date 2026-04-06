#!/usr/bin/env python3
"""Validate SMTP email configuration and send a test email."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.core import get_config
from src.generation.email_service import EmailService


DEFAULT_RECIPIENT = "lkg1116@outlook.com"


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _validate_email_config() -> list[str]:
    automation = get_config().automation
    missing: list[str] = []

    if not automation.smtp_host:
        missing.append("AUTOMATION__SMTP_HOST")
    if not automation.smtp_port:
        missing.append("AUTOMATION__SMTP_PORT")
    if not automation.smtp_username:
        missing.append("AUTOMATION__SMTP_USERNAME")
    if not automation.smtp_password:
        missing.append("AUTOMATION__SMTP_PASSWORD")
    if not automation.smtp_from_email:
        missing.append("AUTOMATION__SMTP_FROM_EMAIL")

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate SMTP email configuration and send a test email.",
    )
    parser.add_argument(
        "--to",
        default=DEFAULT_RECIPIENT,
        help=f"Recipient email address (default: {DEFAULT_RECIPIENT})",
    )
    args = parser.parse_args()

    config = get_config()
    service = EmailService()

    automation = config.automation

    print("Email configuration check")
    print("========================")
    print(f"Provider: SMTP")
    print(f"SMTP host: {automation.smtp_host or '(empty)'}")
    print(f"SMTP port: {automation.smtp_port or '(empty)'}")
    print(f"SMTP username: {automation.smtp_username or '(empty)'}")
    print(f"SMTP password: {_mask_secret(automation.smtp_password)}")
    print(f"From email: {automation.smtp_from_email or '(empty)'}")
    print(f"Target recipient: {args.to}")
    print(f"App environment: {config.app_env}")
    print()

    missing = _validate_email_config()
    if missing:
        print("Configuration is incomplete:")
        for item in missing:
            print(f"- {item}")
        return 1

    print("Step 1/2: testing SMTP connection...")
    if not service.test_connection():
        print("SMTP connection failed.")
        return 3

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = "[GlobalID] SMTP test email"
    body_text = (
        "This is a GlobalID SMTP test email.\n\n"
        f"Sent at: {timestamp}\n"
        f"Provider: SMTP\n"
        f"SMTP host: {automation.smtp_host}\n"
        f"From: {automation.smtp_from_email}\n"
        f"To: {args.to}\n"
    )
    body_html = f"""
<html>
  <body>
    <h2>GlobalID SMTP Test</h2>
    <p>This is a GlobalID SMTP test email.</p>
    <ul>
      <li>Sent at: {timestamp}</li>
      <li>Provider: SMTP</li>
      <li>SMTP host: {automation.smtp_host}</li>
      <li>From: {automation.smtp_from_email}</li>
      <li>To: {args.to}</li>
    </ul>
  </body>
</html>
"""

    print("Step 2/2: sending test email...")
    if not service.send(
        to_addrs=[args.to],
        subject=subject,
        body_html=body_html,
        body_text=body_text,
    ):
        print("Email send failed.")
        return 4

    print("Test email sent successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
