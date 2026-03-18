#!/usr/bin/env python3
"""Validate Microsoft Graph email configuration and send a test email."""

from __future__ import annotations

import argparse
import base64
import json
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


def _decode_token_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        return {}


def _validate_email_config() -> list[str]:
    automation = get_config().automation
    missing: list[str] = []

    if not automation.graph_enabled:
        missing.append("AUTOMATION__GRAPH_ENABLED=true")
    if not automation.graph_tenant_id:
        missing.append("AUTOMATION__GRAPH_TENANT_ID")
    if not automation.graph_client_id:
        missing.append("AUTOMATION__GRAPH_CLIENT_ID")
    if not automation.graph_client_secret:
        missing.append("AUTOMATION__GRAPH_CLIENT_SECRET")
    if not automation.graph_sender_user_id:
        missing.append("AUTOMATION__GRAPH_SENDER_USER_ID")

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Microsoft Graph email configuration and send a test email.",
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
    print(f"Provider: Microsoft Graph")
    print(f"Graph enabled: {automation.graph_enabled}")
    print(f"Tenant ID: {automation.graph_tenant_id or '(empty)'}")
    print(f"Client ID: {automation.graph_client_id or '(empty)'}")
    print(f"Client secret: {_mask_secret(automation.graph_client_secret)}")
    print(f"Sender user ID: {automation.graph_sender_user_id or '(empty)'}")
    print(f"Target recipient: {args.to}")
    print(f"App environment: {config.app_env}")
    print()

    missing = _validate_email_config()
    if missing:
        print("Configuration is incomplete:")
        for item in missing:
            print(f"- {item}")
        return 1

    print("Step 0/2: inspecting access token...")
    try:
        token = service._service.acquire_token()
        claims = _decode_token_claims(token)
        roles = claims.get("roles") or []
        print(f"Token audience: {claims.get('aud', '(unknown)')}")
        print(f"Token app id: {claims.get('appid', '(unknown)')}")
        print(f"Token roles: {roles if roles else '(none)'}")
        if "Mail.Send" not in roles:
            print("Diagnostic: application permission 'Mail.Send' is missing or admin consent has not been granted.")
    except Exception as exc:
        print(f"Failed to inspect token: {exc}")
        return 2
    print()

    print("Step 1/2: testing Microsoft Graph connection...")
    if not service.test_connection():
        print("Microsoft Graph connection failed.")
        return 3

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = "[GlobalID] Graph test email"
    body_text = (
        "This is a GlobalID Microsoft Graph test email.\n\n"
        f"Sent at: {timestamp}\n"
        f"Provider: Microsoft Graph\n"
        f"Sender user ID: {automation.graph_sender_user_id}\n"
        f"To: {args.to}\n"
    )
    body_html = f"""
<html>
  <body>
    <h2>GlobalID Microsoft Graph Test</h2>
    <p>This is a GlobalID Microsoft Graph test email.</p>
    <ul>
      <li>Sent at: {timestamp}</li>
      <li>Provider: Microsoft Graph</li>
      <li>Sender user ID: {automation.graph_sender_user_id}</li>
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
