"""
GlobalID V2 Email Service

邮件服务：发送报告邮件
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from typing import List, Optional

from src.core import get_config, get_logger

logger = get_logger(__name__)


class EmailService:
    """
    邮件服务
    
    功能：
    - 发送HTML格式邮件
    - 附件支持
    - 批量发送
    """
    
    def __init__(self):
        """初始化邮件服务"""
        self.config = get_config()
        
        # SMTP配置
        self.smtp_host = self.config.email.smtp_host
        self.smtp_port = self.config.email.smtp_port
        self.smtp_user = self.config.email.smtp_user
        self.smtp_password = self.config.email.smtp_password
        self.from_addr = self.config.email.from_address or self.smtp_user
        self.use_tls = self.config.email.use_tls
        
        logger.info(f"EmailService initialized (SMTP: {self.smtp_host}:{self.smtp_port})")
    
    def send(
        self,
        to_addrs: List[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        attachments: Optional[List[str]] = None,
        cc_addrs: Optional[List[str]] = None,
        bcc_addrs: Optional[List[str]] = None,
    ) -> bool:
        """
        发送邮件
        
        Args:
            to_addrs: 收件人列表
            subject: 邮件主题
            body_html: HTML正文
            body_text: 纯文本正文（可选）
            attachments: 附件文件路径列表
            cc_addrs: 抄送列表
            bcc_addrs: 密送列表
            
        Returns:
            是否发送成功
        """
        logger.info(f"Sending email to {len(to_addrs)} recipients: {subject}")
        
        try:
            # 创建邮件对象
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_addr
            msg['To'] = ', '.join(to_addrs)
            
            if cc_addrs:
                msg['Cc'] = ', '.join(cc_addrs)
            
            # 添加正文
            if body_text:
                part_text = MIMEText(body_text, 'plain', 'utf-8')
                msg.attach(part_text)
            
            part_html = MIMEText(body_html, 'html', 'utf-8')
            msg.attach(part_html)
            
            # 添加附件
            if attachments:
                for attachment_path in attachments:
                    self._attach_file(msg, attachment_path)
            
            # 连接SMTP服务器并发送
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                
                # 合并所有收件人
                all_recipients = to_addrs.copy()
                if cc_addrs:
                    all_recipients.extend(cc_addrs)
                if bcc_addrs:
                    all_recipients.extend(bcc_addrs)
                
                server.sendmail(self.from_addr, all_recipients, msg.as_string())
            
            logger.info(f"Email sent successfully to {len(to_addrs)} recipients")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def send_report(
        self,
        to_addrs: List[str],
        report_title: str,
        report_html: str,
        pdf_path: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        发送报告邮件（便捷方法）
        
        Args:
            to_addrs: 收件人列表
            report_title: 报告标题
            report_html: 报告HTML内容
            pdf_path: PDF附件路径
            **kwargs: 其他参数
            
        Returns:
            是否发送成功
        """
        logger.info(f"Sending report email: {report_title}")
        
        # 构造邮件主题
        subject = f"[GlobalID] {report_title}"
        
        # 构造邮件正文
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
        
        # 附件列表
        attachments = []
        if pdf_path and Path(pdf_path).exists():
            attachments.append(pdf_path)
        
        # 发送邮件
        return self.send(
            to_addrs=to_addrs,
            subject=subject,
            body_html=body_html,
            attachments=attachments,
            **kwargs
        )
    
    def _attach_file(self, msg: MIMEMultipart, filepath: str) -> None:
        """
        添加附件
        
        Args:
            msg: 邮件对象
            filepath: 附件路径
        """
        filepath = Path(filepath)
        
        if not filepath.exists():
            logger.warning(f"Attachment not found: {filepath}")
            return
        
        try:
            with open(filepath, 'rb') as f:
                part = MIMEApplication(f.read(), Name=filepath.name)
            
            part['Content-Disposition'] = f'attachment; filename="{filepath.name}"'
            msg.attach(part)
            
            logger.debug(f"Attached file: {filepath.name}")
        
        except Exception as e:
            logger.error(f"Failed to attach file {filepath}: {e}")
    
    def test_connection(self) -> bool:
        """
        测试SMTP连接
        
        Returns:
            是否连接成功
        """
        logger.info("Testing SMTP connection...")
        
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.use_tls:
                    server.starttls()
                
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                
                logger.info("SMTP connection successful")
                return True
        
        except Exception as e:
            logger.error(f"SMTP connection failed: {e}")
            return False
