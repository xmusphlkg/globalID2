"""
GlobalID V2 Email Service

邮件服务：统一通过 SMTP 发送邮件
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core import get_logger
from src.services.smtp_email_service import smtp_email_service

logger = get_logger(__name__)


class EmailService:
    """
    统一邮件服务门面。

    兼容原有调用方式，但底层全部走 SMTP。
    """

    def __init__(self) -> None:
        self._service = smtp_email_service
        logger.info("EmailService initialized (provider: SMTP)")

    def is_configured(self) -> bool:
        return self._service.is_configured()

    def send(
        self,
        to_addrs: list[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        cc_addrs: Optional[list[str]] = None,
        bcc_addrs: Optional[list[str]] = None,
    ) -> bool:
        return self._service.send_email(
            recipients=to_addrs,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            attachments=attachments,
            cc_recipients=cc_addrs,
            bcc_recipients=bcc_addrs,
        )

    def send_report(
        self,
        to_addrs: list[str],
        report_title: str,
        report_html: str,
        pdf_path: Optional[str] = None,
        **kwargs,
    ) -> bool:
        logger.info("Sending report email: %s", report_title)

        subject = f"[GlobalID] {report_title}"
        body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
        }}
        .header {{
            background-color: #3498db;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .content {{
            padding: 20px;
        }}
        .footer {{
            background-color: #ecf0f1;
            padding: 15px;
            text-align: center;
            font-size: 12px;
            color: #7f8c8d;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>GlobalID 疾病监测报告</h1>
    </div>
    <div class="content">
        <p>您好，</p>
        <p>这是最新的疾病监测报告：<strong>{report_title}</strong></p>
        <p>报告详情请见附件或下方内容。</p>
        <hr>
        {report_html}
    </div>
    <div class="footer">
        <p>本邮件由 GlobalID V2 系统自动发送</p>
        <p>如有问题，请联系系统管理员</p>
    </div>
</body>
</html>
"""

        attachments: list[str] = []
        if pdf_path and Path(pdf_path).exists():
            attachments.append(pdf_path)

        return self.send(
            to_addrs=to_addrs,
            subject=subject,
            body_html=body_html,
            attachments=attachments,
            **kwargs,
        )

    def test_connection(self) -> bool:
        return self._service.test_connection()

    @staticmethod
    def build_task_failure_html(
        *,
        task_name: str,
        task_uuid: str,
        task_type: str,
        status: str,
        country: str,
        retry_count: int,
        retry_threshold: int,
        priority: str,
        created_at: str,
        started_at: str,
        completed_at: str,
        last_error: str,
        input_data_json: str,
        workbook_entries_html: str,
        error_log_html: str,
    ) -> str:
        """Build standardized HTML for task failure notification emails."""
        return f"""
<html>
  <body style="font-family:Segoe UI,Arial,sans-serif;line-height:1.5;color:#1f2937">
    <h2 style="margin-bottom:8px;color:#dc2626">GlobalID task failure alert</h2>
    <p>The task exceeded the configured retry threshold and needs attention.</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
      <tr><td style="font-weight:bold;width:140px">Task</td><td>{task_name}</td></tr>
      <tr><td style="font-weight:bold">Task UUID</td><td><code>{task_uuid}</code></td></tr>
      <tr><td style="font-weight:bold">Type</td><td>{task_type}</td></tr>
      <tr><td style="font-weight:bold">Status</td><td>{status}</td></tr>
      <tr><td style="font-weight:bold">Country</td><td>{country}</td></tr>
      <tr><td style="font-weight:bold">Retry Count</td><td>{retry_count} / threshold {retry_threshold}</td></tr>
      <tr><td style="font-weight:bold">Priority</td><td>{priority}</td></tr>
      <tr><td style="font-weight:bold">Created</td><td>{created_at}</td></tr>
      <tr><td style="font-weight:bold">Started</td><td>{started_at}</td></tr>
      <tr><td style="font-weight:bold">Completed</td><td>{completed_at}</td></tr>
      <tr><td style="font-weight:bold;color:#dc2626">Last Error</td><td style="color:#dc2626">{last_error}</td></tr>
    </table>
    <h3>Input Data</h3>
    <pre style="white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:8px;font-size:12px">{input_data_json}</pre>
    <h3>Recent Workbook Entries</h3>
    <ol>{workbook_entries_html or "<li>No workbook entries found.</li>"}</ol>
    <h3>Recent Error Log</h3>
    <pre style="white-space:pre-wrap;background:#111827;color:#f9fafb;padding:12px;border-radius:8px;font-size:12px">{error_log_html}</pre>
    <hr style="margin-top:24px;border:none;border-top:1px solid #e5e7eb"/>
    <p style="font-size:12px;color:#6b7280">This email was sent automatically by the GlobalID monitoring system. Log files are attached for detailed diagnostics.</p>
  </body>
</html>
"""

    @staticmethod
    def build_task_retry_warning_html(
        *,
        task_name: str,
        task_uuid: str,
        task_type: str,
        country: str,
        retry_count: int,
        retry_threshold: int,
        last_error: str,
    ) -> str:
        """Build standardized HTML for task retry warning emails."""
        return f"""
<html>
  <body style="font-family:Segoe UI,Arial,sans-serif;line-height:1.5;color:#1f2937">
    <h2 style="margin-bottom:8px;color:#f59e0b">GlobalID task retry warning</h2>
    <p>A task has encountered errors and is being retried automatically.</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
      <tr><td style="font-weight:bold;width:140px">Task</td><td>{task_name}</td></tr>
      <tr><td style="font-weight:bold">Task UUID</td><td><code>{task_uuid}</code></td></tr>
      <tr><td style="font-weight:bold">Type</td><td>{task_type}</td></tr>
      <tr><td style="font-weight:bold">Country</td><td>{country}</td></tr>
      <tr><td style="font-weight:bold">Retry Count</td><td>{retry_count} / threshold {retry_threshold}</td></tr>
      <tr><td style="font-weight:bold;color:#f59e0b">Last Error</td><td style="color:#f59e0b">{last_error}</td></tr>
    </table>
    <p>The system will continue retrying up to <strong>{retry_threshold}</strong> attempts. If all retries fail, a final failure notification will be sent.</p>
    <hr style="margin-top:24px;border:none;border-top:1px solid #e5e7eb"/>
    <p style="font-size:12px;color:#6b7280">This email was sent automatically by the GlobalID monitoring system.</p>
  </body>
</html>
"""

    def send_subscription_email(
        self,
        to_addrs: list[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        cc_addrs: Optional[list[str]] = None,
        bcc_addrs: Optional[list[str]] = None,
    ) -> bool:
        """Send subscription-related emails to users.
        
        This is the primary method for sending emails to subscribers,
        including reports, alerts, and notifications with optional attachments.
        """
        logger.info("Sending subscription email to %d recipients: %s", len(to_addrs), subject)
        return self.send(
            to_addrs=to_addrs,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            attachments=attachments,
            cc_addrs=cc_addrs,
            bcc_addrs=bcc_addrs,
        )
