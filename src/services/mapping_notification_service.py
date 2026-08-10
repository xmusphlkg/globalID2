"""Transactional email delivery for disease-mapping review events."""

from __future__ import annotations

import asyncio
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select

from src.core import get_database, get_logger
from src.domain import MappingNotificationOutbox
from src.services.settings_service import system_settings_service
from src.services.smtp_email_service import smtp_email_service

logger = get_logger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    retryable: bool
    provider: str
    response: dict[str, Any]
    error: str | None = None


class MappingEmailTransport:
    """Provider adapter with SMTP as the safe configured default.

    Cloudflare Email Sending is opt-in via ``MAPPING_EMAIL_PROVIDER=cloudflare``
    because the account token alone does not prove that a sending domain has
    been onboarded.  The REST request follows Cloudflare's external-app schema.
    """

    def configured_provider(self) -> str:
        requested = os.getenv("MAPPING_EMAIL_PROVIDER", "smtp").strip().lower()
        return requested if requested in {"smtp", "cloudflare"} else "smtp"

    async def send(
        self,
        *,
        provider: str,
        recipients: list[str],
        subject: str,
        body_text: str,
        body_html: str,
    ) -> DeliveryResult:
        selected = self.configured_provider() if provider == "auto" else provider
        if selected == "cloudflare":
            return await self._send_cloudflare(
                recipients=recipients,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
        if not smtp_email_service.is_configured():
            return DeliveryResult(False, False, "smtp", {}, "SMTP is not configured")
        try:
            sent = await asyncio.to_thread(
                smtp_email_service.send_email,
                recipients=recipients,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                raise_on_error=True,
            )
            return DeliveryResult(
                bool(sent), False, "smtp", {"accepted": recipients if sent else []},
                None if sent else "SMTP provider rejected the message",
            )
        except Exception as exc:
            retryable = self._smtp_retryable(exc)
            return DeliveryResult(
                False,
                retryable,
                "smtp",
                {"exception_type": type(exc).__name__, "retryable": retryable},
                str(exc),
            )

    @staticmethod
    def _smtp_retryable(exc: Exception) -> bool:
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
                TimeoutError,
                OSError,
                smtplib.SMTPConnectError,
                smtplib.SMTPServerDisconnected,
            ),
        )

    async def _send_cloudflare(
        self,
        *,
        recipients: list[str],
        subject: str,
        body_text: str,
        body_html: str,
    ) -> DeliveryResult:
        cloudflare = system_settings_service.cloudflare_runtime()
        smtp = system_settings_service.smtp_runtime()
        token = str(cloudflare.get("cloudflare_api_token") or "").strip()
        account_id = str(cloudflare.get("cloudflare_account_id") or "").strip()
        from_address = os.getenv("CLOUDFLARE_EMAIL_FROM", "").strip() or str(
            smtp.get("smtp_from_email") or ""
        ).strip()
        if not token or not account_id or not from_address:
            return DeliveryResult(
                False, False, "cloudflare", {},
                "Cloudflare Email requires account id, API token, and a verified from address",
            )
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/email/sending/send"
        payload = {
            "to": recipients,
            "from": {
                "address": from_address,
                "name": os.getenv("MAPPING_EMAIL_FROM_NAME", "GIDS Disease Mapping"),
            },
            "subject": subject,
            "text": body_text,
            "html": body_html,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                )
            try:
                response_payload = response.json()
            except Exception:
                response_payload = {"body": response.text[:1000]}
            success = response.status_code == 200 and bool(response_payload.get("success", True))
            retryable = response.status_code == 429 or response.status_code >= 500
            return DeliveryResult(
                success,
                retryable,
                "cloudflare",
                response_payload,
                None if success else f"Cloudflare Email HTTP {response.status_code}",
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return DeliveryResult(False, True, "cloudflare", {}, str(exc))


class MappingNotificationService:
    def __init__(self) -> None:
        self.transport = MappingEmailTransport()

    async def process_once(self, limit: int = 50) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        async with get_database() as db:
            stale_sends = (
                await db.execute(
                    select(MappingNotificationOutbox).where(
                        MappingNotificationOutbox.status == "sending",
                        MappingNotificationOutbox.updated_at
                        < (now - timedelta(minutes=10)).replace(tzinfo=None),
                    )
                )
            ).scalars().all()
            for row in stale_sends:
                row.status = "retry"
                row.next_attempt_at = now
                row.last_error = "Recovered stale delivery claim after service interruption"
                row.metadata_ = {**(row.metadata_ or {}), "recovered_stale_send": True}
            # Recover rows created before transient SMTP exceptions were
            # classified correctly.  Permanent authentication/address errors
            # remain dead and require configuration changes.
            legacy_dead = (
                await db.execute(
                    select(MappingNotificationOutbox).where(
                        MappingNotificationOutbox.status == "dead",
                        MappingNotificationOutbox.attempts < 5,
                    )
                )
            ).scalars().all()
            for row in legacy_dead:
                message = str(row.last_error or "").casefold()
                legacy_boolean_failure = (
                    message == "smtp provider rejected the message"
                    and str((row.metadata_ or {}).get("delivered_provider") or "") == "smtp"
                    and int(row.attempts or 0) <= 1
                )
                if legacy_boolean_failure or any(
                    token in message for token in ("timed out", "unexpectedly closed", "connection reset")
                ):
                    row.status = "retry"
                    row.next_attempt_at = now
                    row.metadata_ = {**(row.metadata_ or {}), "recovered_transient_dead_letter": True}
            if legacy_dead or stale_sends:
                await db.commit()
            rows = (
                await db.execute(
                    select(MappingNotificationOutbox)
                    .where(
                        MappingNotificationOutbox.status.in_(["pending", "retry"]),
                        or_(
                            MappingNotificationOutbox.next_attempt_at.is_(None),
                            MappingNotificationOutbox.next_attempt_at <= now,
                        ),
                    )
                    .order_by(MappingNotificationOutbox.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).scalars().all()
            if not rows:
                return {"claimed": 0, "sent": 0, "retry": 0, "dead": 0}
            for row in rows:
                row.status = "sending"
                row.attempts = int(row.attempts or 0) + 1
            await db.commit()
            claimed_ids = [row.id for row in rows]

        groups: dict[tuple[str, str, tuple[str, ...]], list[MappingNotificationOutbox]] = {}
        for row in rows:
            recipients = tuple(sorted({str(item).strip() for item in (row.recipients or []) if str(item).strip()}))
            groups.setdefault((row.aggregate_key, row.provider, recipients), []).append(row)

        counters = {"claimed": len(rows), "sent": 0, "retry": 0, "dead": 0}
        for (_aggregate, provider, recipient_tuple), items in groups.items():
            recipients = list(recipient_tuple)
            if not recipients:
                result = DeliveryResult(False, False, provider, {}, "No recipients configured")
            else:
                subject = items[0].subject if len(items) == 1 else f"{items[0].subject} (+{len(items) - 1} more)"
                body_text = "\n\n---\n\n".join(item.body_text for item in items)
                body_html = "<hr>".join(item.body_html for item in items)
                result = await self.transport.send(
                    provider=provider,
                    recipients=recipients,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                )

            async with get_database() as db:
                persisted = (
                    await db.execute(
                        select(MappingNotificationOutbox)
                        .where(MappingNotificationOutbox.id.in_([item.id for item in items]))
                        .with_for_update()
                    )
                ).scalars().all()
                for row in persisted:
                    row.provider_response = result.response
                    row.last_error = result.error
                    row.metadata_ = {**(row.metadata_ or {}), "delivered_provider": result.provider}
                    if result.success:
                        row.status = "sent"
                        row.sent_at = datetime.now(timezone.utc)
                        counters["sent"] += 1
                    elif result.retryable and row.attempts < 5:
                        row.status = "retry"
                        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                            minutes=min(60, 2 ** max(0, row.attempts - 1))
                        )
                        counters["retry"] += 1
                    else:
                        row.status = "dead"
                        counters["dead"] += 1
                await db.commit()
        return counters


mapping_notification_service = MappingNotificationService()


__all__ = [
    "DeliveryResult",
    "MappingEmailTransport",
    "MappingNotificationService",
    "mapping_notification_service",
]
