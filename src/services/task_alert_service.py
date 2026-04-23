"""Unified task alert service.

Sends a plain admin email whenever any task finishes with a terminal
status of FAILED or CANCELLED.  Intentionally decoupled from automation
job logic so that it covers every task type (crawl, report, export,
data-release, …) without any retry-threshold gating.

"数据不需要更新" tasks are naturally excluded: when content is already
up-to-date the platform either never enqueues a task, or the task
completes with status COMPLETED — neither case triggers this service.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from src.core import get_database, get_logger
from src.domain import Country, Task, TaskStatus, TaskWorkbook
from src.services.smtp_email_service import smtp_email_service
from src.services.settings_service import system_settings_service

logger = get_logger(__name__)

# Metadata key pattern: "alert_sent_failed" / "alert_sent_cancelled"
_DEDUP_KEY_TMPL = "alert_sent_{status}"


class TaskAlertService:
    """Send a single admin alert email when a task fails or is cancelled."""

    async def send_task_alert(
        self,
        task_uuid: str,
        final_status: TaskStatus,
    ) -> None:
        """Send an admin alert for *task_uuid* reaching *final_status*.

        Safe to call even when SMTP is not configured — it silently returns.
        Idempotent: a second call for the same task+status is a no-op.

        Args:
            task_uuid:    UUID of the task that just reached a terminal state.
            final_status: Must be FAILED or CANCELLED; any other value is ignored.
        """
        if final_status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return

        smtp_state = system_settings_service.build_smtp_status()
        admin_emails = smtp_state["admin_emails"]
        if not admin_emails or not smtp_state["alerting_ready"]:
            return

        async with get_database() as db:
            task = (
                await db.execute(
                    select(Task).where(Task.task_uuid == task_uuid)
                )
            ).scalar_one_or_none()
            if task is None:
                logger.warning("task_alert_service: task %s not found", task_uuid)
                return

            # Idempotency guard — don't send a second alert for same task+status.
            dedup_key = _DEDUP_KEY_TMPL.format(status=final_status.value)
            metadata = dict(task.metadata_ or {})
            if metadata.get(dedup_key):
                return

            workbook_entries: list[TaskWorkbook] = list(
                reversed(
                    (
                        await db.execute(
                            select(TaskWorkbook)
                            .where(TaskWorkbook.task_id == task.id)
                            .order_by(TaskWorkbook.created_at.desc())
                            .limit(15)
                        )
                    ).scalars().all()
                )
            )

            country = (
                await db.get(Country, task.country_id) if task.country_id else None
            )

            status_label = final_status.value  # "failed" or "cancelled"
            subject = f"[GlobalID Alert] Task {status_label}: {task.task_name}"
            body = _build_alert_html(task, country, workbook_entries, final_status)

            sent = smtp_email_service.send_email(
                recipients=admin_emails,
                subject=subject,
                body_html=body,
            )
            if not sent:
                return

            metadata[dedup_key] = datetime.now(timezone.utc).isoformat()
            task.metadata_ = metadata
            await db.commit()

        logger.info(
            "Task alert (%s) sent for %s to %d recipient(s)",
            status_label,
            task_uuid,
            len(admin_emails),
        )


# ── HTML builder ──────────────────────────────────────────────────────────────

def _fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _build_alert_html(
    task: Task,
    country: Optional[Country],
    workbook_entries: list[TaskWorkbook],
    final_status: TaskStatus,
) -> str:
    is_failure = final_status == TaskStatus.FAILED
    accent_color = "#dc2626" if is_failure else "#f59e0b"
    heading = "Task failure alert" if is_failure else "Task cancellation alert"
    subtitle = (
        "A task has failed and may require manual attention."
        if is_failure
        else "A task was cancelled before it could complete."
    )

    entry_items = ""
    for entry in workbook_entries:
        t = html.escape(entry.title or entry.entry_type or "log")
        c = html.escape((entry.content or "").strip()[:2000])
        entry_items += (
            f"<li style='margin-bottom:8px'>"
            f"<strong>{t}</strong><br/>"
            f"<pre style='white-space:pre-wrap;font-size:12px;margin:4px 0'>{c}</pre>"
            f"</li>"
        )

    task_type_str = html.escape(
        str(task.task_type.value if hasattr(task.task_type, "value") else task.task_type)
    )
    input_json = html.escape(
        json.dumps(task.input_data or {}, ensure_ascii=False, indent=2)
    )

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Segoe UI,Arial,sans-serif;line-height:1.6;color:#1f2937;max-width:780px;margin:0 auto">
  <div style="background:{accent_color};color:#fff;padding:16px 20px;border-radius:8px 8px 0 0">
    <h2 style="margin:0;font-size:18px">GlobalID — {heading}</h2>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:20px">
    <p style="margin-top:0">{subtitle}</p>
    <table cellpadding="7" cellspacing="0"
           style="border-collapse:collapse;width:100%;font-size:14px">
      <tr style="background:#f9fafb">
        <td style="font-weight:bold;width:140px;border:1px solid #e5e7eb">Task</td>
        <td style="border:1px solid #e5e7eb">{html.escape(task.task_name or "-")}</td>
      </tr>
      <tr>
        <td style="font-weight:bold;border:1px solid #e5e7eb">Task UUID</td>
        <td style="border:1px solid #e5e7eb"><code style="font-size:13px">{html.escape(task.task_uuid)}</code></td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="font-weight:bold;border:1px solid #e5e7eb">Type</td>
        <td style="border:1px solid #e5e7eb">{task_type_str}</td>
      </tr>
      <tr>
        <td style="font-weight:bold;border:1px solid #e5e7eb">Status</td>
        <td style="border:1px solid #e5e7eb;color:{accent_color};font-weight:bold">
          {html.escape(final_status.value.upper())}
        </td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="font-weight:bold;border:1px solid #e5e7eb">Country</td>
        <td style="border:1px solid #e5e7eb">{html.escape(country.code if country else "-")}</td>
      </tr>
      <tr>
        <td style="font-weight:bold;border:1px solid #e5e7eb">Created</td>
        <td style="border:1px solid #e5e7eb">{_fmt_dt(task.created_at)}</td>
      </tr>
      <tr style="background:#f9fafb">
        <td style="font-weight:bold;border:1px solid #e5e7eb">Started</td>
        <td style="border:1px solid #e5e7eb">{_fmt_dt(task.started_at)}</td>
      </tr>
      <tr>
        <td style="font-weight:bold;border:1px solid #e5e7eb">Completed</td>
        <td style="border:1px solid #e5e7eb">{_fmt_dt(task.completed_at)}</td>
      </tr>
      <tr style="background:#fff0f0">
        <td style="font-weight:bold;border:1px solid #e5e7eb;color:{accent_color}">
          Last Error
        </td>
        <td style="border:1px solid #e5e7eb;color:{accent_color}">
          {html.escape(task.last_error or "-")}
        </td>
      </tr>
    </table>

    <h3 style="margin-top:20px;font-size:15px">Input Data</h3>
    <pre style="white-space:pre-wrap;background:#f8fafc;padding:12px;
                border-radius:6px;font-size:12px;max-height:300px;overflow:auto">{input_json}</pre>

    <h3 style="font-size:15px">Recent Workbook Entries</h3>
    <ol style="padding-left:20px">
      {entry_items or "<li>No workbook entries found.</li>"}
    </ol>

    <hr style="margin-top:24px;border:none;border-top:1px solid #e5e7eb"/>
    <p style="font-size:12px;color:#6b7280;margin-bottom:0">
      Sent automatically by the GlobalID monitoring system.
    </p>
  </div>
</body>
</html>"""


# Module-level singleton — import this everywhere.
task_alert_service = TaskAlertService()
