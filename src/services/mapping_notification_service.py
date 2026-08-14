"""Transactional email delivery for disease-mapping review events."""

from __future__ import annotations

import asyncio
from collections import Counter
import html
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, or_, select

from src.core import get_database, get_logger
from src.domain import (
    DiseaseMappingCandidate,
    MappingNotificationOutbox,
    SourceDiseaseCategory,
)
from src.services.settings_service import system_settings_service
from src.services.smtp_email_service import smtp_email_service

logger = get_logger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _next_digest_at(
    now: datetime,
    latest_sent_at: datetime | None,
    *,
    hour_utc: int,
) -> datetime:
    """Return now when today's digest is due, otherwise its next UTC slot."""

    today_slot = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    latest = _aware(latest_sent_at)
    if now < today_slot:
        return today_slot
    if latest is None or latest < today_slot:
        return now
    return today_slot + timedelta(days=1)


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

    async def process_once(
        self,
        limit: int | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Deliver one readable digest instead of one email per mapping event."""

        now = datetime.now(timezone.utc)
        if not _env_bool("MAPPING_EMAIL_DIGEST_ENABLED", True):
            return {
                "claimed": 0,
                "sent": 0,
                "retry": 0,
                "dead": 0,
                "messages_sent": 0,
                "digest_disabled": True,
            }
        hour_utc = _env_int(
            "MAPPING_EMAIL_DIGEST_HOUR_UTC", 1, minimum=0, maximum=23
        )
        effective_limit = min(
            max(1, int(limit or 2000)),
            _env_int(
                "MAPPING_EMAIL_DIGEST_MAX_EVENTS",
                2000,
                minimum=1,
                maximum=10000,
            ),
        )
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
            latest_sent_at = (
                await db.execute(select(func.max(MappingNotificationOutbox.sent_at)))
            ).scalar_one_or_none()
            next_digest_at = _next_digest_at(
                now,
                latest_sent_at,
                hour_utc=hour_utc,
            )
            if not force and next_digest_at > now:
                pending = int(
                    (
                        await db.execute(
                            select(func.count())
                            .select_from(MappingNotificationOutbox)
                            .where(
                                MappingNotificationOutbox.status.in_(["pending", "retry"])
                            )
                        )
                    ).scalar_one()
                    or 0
                )
                return {
                    "claimed": 0,
                    "sent": 0,
                    "retry": 0,
                    "dead": 0,
                    "messages_sent": 0,
                    "deferred_events": pending,
                    "next_digest_at": next_digest_at.isoformat(),
                }
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
                    .limit(effective_limit)
                )
            ).scalars().all()
            if not rows:
                return {
                    "claimed": 0,
                    "sent": 0,
                    "retry": 0,
                    "dead": 0,
                    "messages_sent": 0,
                    "next_digest_at": (
                        now.replace(
                            hour=hour_utc,
                            minute=0,
                            second=0,
                            microsecond=0,
                        )
                        + timedelta(days=1)
                    ).isoformat(),
                }
            for row in rows:
                row.status = "sending"
                row.attempts = int(row.attempts or 0) + 1
            await db.commit()
            claimed_ids = [row.id for row in rows]

        groups: dict[tuple[str, tuple[str, ...]], list[MappingNotificationOutbox]] = {}
        for row in rows:
            recipients = tuple(sorted({str(item).strip() for item in (row.recipients or []) if str(item).strip()}))
            groups.setdefault((row.provider, recipients), []).append(row)

        counters = {
            "claimed": len(rows),
            "sent": 0,
            "retry": 0,
            "dead": 0,
            "messages_sent": 0,
        }
        for (provider, recipient_tuple), items in groups.items():
            recipients = list(recipient_tuple)
            if not recipients:
                result = DeliveryResult(False, False, provider, {}, "No recipients configured")
            else:
                subject, body_text, body_html = await self._digest_content(items)
                result = await self.transport.send(
                    provider=provider,
                    recipients=recipients,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                )
                if result.success:
                    counters["messages_sent"] += 1

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
        counters["next_digest_at"] = (
            now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        ).isoformat()
        return counters

    @staticmethod
    async def _digest_content(
        items: list[MappingNotificationOutbox],
    ) -> tuple[str, str, str]:
        category_ids = sorted(
            {
                int((item.metadata_ or {}).get("category_id"))
                for item in items
                if (item.metadata_ or {}).get("category_id") is not None
            }
        )
        async with get_database() as db:
            categories = (
                (
                    await db.execute(
                        select(SourceDiseaseCategory).where(
                            SourceDiseaseCategory.id.in_(category_ids)
                        )
                    )
                ).scalars().all()
                if category_ids
                else []
            )
            status_counts = dict(
                (
                    await db.execute(
                        select(
                            SourceDiseaseCategory.ai_status,
                            func.count(SourceDiseaseCategory.id),
                        ).group_by(SourceDiseaseCategory.ai_status)
                    )
                ).all()
            )
            proposed_candidates = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(DiseaseMappingCandidate)
                        .where(DiseaseMappingCandidate.status == "proposed")
                    )
                ).scalar_one()
                or 0
            )

        by_id = {category.id: category for category in categories}
        event_counts = Counter(item.event_type for item in items)
        country_counts: Counter[str] = Counter()
        detail_rows: list[tuple[str, str, str, int]] = []
        for item in items:
            category_id = (item.metadata_ or {}).get("category_id")
            category = by_id.get(int(category_id)) if category_id is not None else None
            country = str(getattr(category, "country_code", None) or "—")
            label = str(
                getattr(category, "canonical_source_label", None)
                or item.subject
                or "未命名来源项"
            )
            event_label = (
                "AI 候选已生成"
                if item.event_type == "ai_mapping_suggestion_ready"
                else "发现新来源疾病项"
            )
            candidates = int((item.metadata_ or {}).get("candidate_count") or 0)
            country_counts[country] += 1
            detail_rows.append((country, label, event_label, candidates))

        new_count = int(event_counts.get("new_source_category", 0))
        ready_count = int(event_counts.get("ai_mapping_suggestion_ready", 0))
        subject = (
            f"[GIDS] 疾病映射每日摘要：{ready_count} 条候选可审核，"
            f"{new_count} 个新来源项"
        )
        dashboard_url = os.getenv("MAPPING_REVIEW_DASHBOARD_URL", "").strip()
        review_location = dashboard_url or "控制面板 → 数据治理 → 疾病映射（/ai/disease-mapping）"
        country_summary = "、".join(
            f"{country} {count}"
            for country, count in country_counts.most_common()
        ) or "无"
        current_summary = (
            f"失败待重试 {int(status_counts.get('failed', 0))}；"
            f"处理中 {int(status_counts.get('processing', 0))}；"
            f"AI 已完成 {int(status_counts.get('completed', 0))}；"
            f"待人工审核候选 {proposed_candidates}"
        )
        visible_rows = detail_rows[:30]
        text_lines = [
            "GIDS 疾病映射审核摘要",
            "",
            "这是一封定时汇总邮件，不需要逐条处理通知。",
            f"本期事件：AI 候选已生成 {ready_count} 条；新来源项 {new_count} 个。",
            f"国家/地区分布：{country_summary}",
            f"当前队列：{current_summary}",
            "",
            "建议操作：",
            f"1. 打开 {review_location}",
            "2. 优先查看高置信度候选和失败重试项。",
            "3. 只有人工确认后再点击“接受并发布”；不确定的项目可以继续保留在隔离区。",
            "",
            "本期明细（最多 30 条）：",
        ]
        text_lines.extend(
            f"- [{country}] {label}｜{event_label}｜候选 {candidates}"
            for country, label, event_label, candidates in visible_rows
        )
        if len(detail_rows) > len(visible_rows):
            text_lines.append(
                f"- 另有 {len(detail_rows) - len(visible_rows)} 条，请在控制面板查看。"
            )

        country_html = "".join(
            f"<tr><td>{html.escape(country)}</td><td style='text-align:right'>{count}</td></tr>"
            for country, count in country_counts.most_common()
        )
        detail_html = "".join(
            "<tr>"
            f"<td>{html.escape(country)}</td>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{html.escape(event_label)}</td>"
            f"<td style='text-align:right'>{candidates}</td>"
            "</tr>"
            for country, label, event_label, candidates in visible_rows
        )
        link_html = (
            f"<a href='{html.escape(dashboard_url, quote=True)}'>打开疾病映射控制面板</a>"
            if dashboard_url
            else html.escape(review_location)
        )
        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:900px;color:#172033;line-height:1.55">
          <h1 style="font-size:22px">GIDS 疾病映射审核摘要</h1>
          <p style="background:#eef6ff;padding:12px;border-radius:8px">
            这是一封定时汇总邮件，不需要逐条处理通知。
          </p>
          <h2 style="font-size:17px">本期概览</h2>
          <ul>
            <li>AI 候选已生成：<b>{ready_count}</b></li>
            <li>新来源疾病项：<b>{new_count}</b></li>
            <li>当前队列：{html.escape(current_summary)}</li>
          </ul>
          <h2 style="font-size:17px">应该怎么处理</h2>
          <ol>
            <li>{link_html}</li>
            <li>优先检查高置信度候选和失败重试项。</li>
            <li>人工确认后再“接受并发布”；不确定项继续留在隔离区。</li>
          </ol>
          <h2 style="font-size:17px">国家/地区分布</h2>
          <table style="border-collapse:collapse;width:360px" border="1" cellpadding="6">
            <thead><tr><th>国家/地区</th><th>事件数</th></tr></thead>
            <tbody>{country_html}</tbody>
          </table>
          <h2 style="font-size:17px">本期明细（最多 30 条）</h2>
          <table style="border-collapse:collapse;width:100%" border="1" cellpadding="6">
            <thead><tr><th>国家/地区</th><th>来源疾病项</th><th>状态</th><th>候选数</th></tr></thead>
            <tbody>{detail_html}</tbody>
          </table>
          {f'<p>另有 {len(detail_rows) - len(visible_rows)} 条，请在控制面板查看。</p>' if len(detail_rows) > len(visible_rows) else ''}
        </div>
        """
        return subject, "\n".join(text_lines), body_html


mapping_notification_service = MappingNotificationService()


__all__ = [
    "DeliveryResult",
    "MappingEmailTransport",
    "MappingNotificationService",
    "mapping_notification_service",
]
