"""Schemas for unified runtime settings."""

from typing import Optional

from pydantic import BaseModel


class SmtpSettingsOut(BaseModel):
    source: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_from_email: str
    smtp_use_tls: bool
    admin_emails_raw: str
    admin_emails: list[str] = []
    smtp_password_present: bool
    smtp_configured: bool
    alerting_ready: bool


class GithubSettingsOut(BaseModel):
    source: str
    github_data_share_repo_url: str
    github_data_share_repo_branch: str
    github_data_share_raw_base_url: str
    github_data_share_raw_base_url_effective: str
    raw_archive_enabled: bool
    raw_archive_repo_url: str
    raw_archive_branch: str
    raw_archive_configured: bool
    default_github_remote: str
    default_github_branch: str
    github_configured: bool
    release_defaults_ready: bool


class CloudflareSettingsOut(BaseModel):
    source: str
    cloudflare_api_token_present: bool
    cloudflare_account_id_present: bool
    default_cloudflare_project_name: str
    cloudflare_configured: bool


class RuntimeSettingsOut(BaseModel):
    smtp: SmtpSettingsOut
    github: GithubSettingsOut
    cloudflare: CloudflareSettingsOut


class SmtpSettingsUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    admin_emails_raw: Optional[str] = None


class GithubSettingsUpdate(BaseModel):
    github_data_share_repo_url: Optional[str] = None
    github_data_share_repo_branch: Optional[str] = None
    github_data_share_raw_base_url: Optional[str] = None
    raw_archive_enabled: Optional[bool] = None
    raw_archive_repo_url: Optional[str] = None
    raw_archive_branch: Optional[str] = None
    default_github_remote: Optional[str] = None
    default_github_branch: Optional[str] = None


class CloudflareSettingsUpdate(BaseModel):
    cloudflare_api_token: Optional[str] = None
    cloudflare_account_id: Optional[str] = None
    default_cloudflare_project_name: Optional[str] = None


class TestEmailRequest(BaseModel):
    recipient: str
