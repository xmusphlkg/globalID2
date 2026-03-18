"""Microsoft Graph email delivery helpers."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Iterable, Optional

import requests

from src.core import get_config, get_logger

logger = get_logger(__name__)


class GraphEmailService:
    """Send email via Microsoft Graph using client credentials."""

    def __init__(self) -> None:
        self._config = get_config

    def is_configured(self) -> bool:
        config = self._config().automation
        return bool(
            config.graph_enabled
            and config.graph_tenant_id
            and config.graph_client_id
            and config.graph_client_secret
            and config.graph_sender_user_id
        )

    def acquire_token(self) -> str:
        config = self._config().automation
        if not self.is_configured():
            raise RuntimeError("Microsoft Graph email is not fully configured")
        token_url = (
            f"https://login.microsoftonline.com/"
            f"{config.graph_tenant_id}/oauth2/v2.0/token"
        )
        response = requests.post(
            token_url,
            data={
                "client_id": config.graph_client_id,
                "client_secret": config.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Microsoft Graph token response did not include access_token")
        return token

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
        config = self._config().automation
        recipient_list = [addr.strip() for addr in recipients if addr and addr.strip()]
        if not recipient_list:
            logger.warning("Graph email skipped: no recipients configured")
            return False

        try:
            access_token = self.acquire_token()
            endpoint = (
                f"https://graph.microsoft.com/v1.0/users/"
                f"{config.graph_sender_user_id}/sendMail"
            )
            body_content = body_html
            if not body_content and body_text:
                body_content = f"<pre>{body_text}</pre>"

            email_msg = {
                "Message": {
                    "Subject": subject,
                    "Body": {"ContentType": "HTML", "Content": body_content},
                    "ToRecipients": [
                        {"EmailAddress": {"Address": recipient}}
                        for recipient in recipient_list
                    ],
                },
                "SaveToSentItems": "true",
            }
            cc_list = [addr.strip() for addr in (cc_recipients or []) if addr and addr.strip()]
            bcc_list = [addr.strip() for addr in (bcc_recipients or []) if addr and addr.strip()]
            if cc_list:
                email_msg["Message"]["CcRecipients"] = [
                    {"EmailAddress": {"Address": recipient}}
                    for recipient in cc_list
                ]
            if bcc_list:
                email_msg["Message"]["BccRecipients"] = [
                    {"EmailAddress": {"Address": recipient}}
                    for recipient in bcc_list
                ]

            attachment_payload = self._build_attachments(attachments or [])
            if attachment_payload:
                email_msg["Message"]["Attachments"] = attachment_payload

            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=email_msg,
                timeout=30,
            )
            response.raise_for_status()
            logger.info(f"Sent Graph email to {len(recipient_list)} recipient(s)")
            return True
        except Exception as exc:
            logger.error(f"Failed to send Graph email: {exc}")
            return False

    def test_connection(self) -> bool:
        config = self._config().automation
        try:
            access_token = self.acquire_token()
            endpoint = f"https://graph.microsoft.com/v1.0/users/{config.graph_sender_user_id}"
            response = requests.get(
                endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Microsoft Graph email connection successful")
            return True
        except Exception as exc:
            logger.error(f"Microsoft Graph email connection failed: {exc}")
            return False

    def _build_attachments(self, attachments: Iterable[str]) -> list[dict]:
        payload: list[dict] = []
        for attachment_path in attachments:
            path = Path(attachment_path)
            if not path.exists():
                logger.warning(f"Attachment not found: {path}")
                continue
            try:
                content_bytes = base64.b64encode(path.read_bytes()).decode("ascii")
            except Exception as exc:
                logger.error(f"Failed to read attachment {path}: {exc}")
                continue

            payload.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": path.name,
                    "contentBytes": content_bytes,
                }
            )
        return payload


graph_email_service = GraphEmailService()
