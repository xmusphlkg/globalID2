"""
GlobalID V2 Email Service

邮件服务：统一通过 Microsoft Graph 发送邮件
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core import get_logger
from src.services.graph_email_service import graph_email_service

logger = get_logger(__name__)


class EmailService:
    """
    统一邮件服务门面。

    兼容原有调用方式，但底层全部走 Microsoft Graph。
    """

    def __init__(self) -> None:
        self._service = graph_email_service
        logger.info("EmailService initialized (provider: Microsoft Graph)")

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
