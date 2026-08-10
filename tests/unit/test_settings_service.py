import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.services.settings_service import RuntimeSettingsService
import pytest


def _fake_config(tmp_path: Path):
    return SimpleNamespace(
        data_dir=tmp_path,
        automation=SimpleNamespace(
            smtp_host="smtp.env.local",
            smtp_port=587,
            smtp_username="env-user",
            smtp_password="env-secret",
            smtp_from_email="env-alerts@example.com",
            smtp_use_tls=True,
            admin_emails_raw="env-ops@example.com, env-dev@example.com",
        ),
        data_release=SimpleNamespace(
            default_github_remote="origin",
            default_github_branch="main",
            default_cloudflare_project_name="globalid",
        ),
        github_data_share_repo_url="git@github.com:env-owner/env-repo.git",
        github_data_share_repo_branch="release",
        github_data_share_raw_base_url="",
        raw_archive=SimpleNamespace(
            enabled=True,
            repo_url="git@github.com:env-owner/raw-archive.git",
            branch="main",
            repository_dir=tmp_path / "raw-archive",
            git_timeout_seconds=1800,
        ),
        cloudflare_api_token="env-cloudflare-token",
        cloudflare_account_id="env-account-id",
        public_ga4_measurement_id="G-8P39XV52NC",
    )


def test_public_snapshot_uses_env_defaults(monkeypatch, tmp_path):
    cfg = _fake_config(tmp_path)
    monkeypatch.setattr("src.services.settings_service.get_config", lambda: cfg)

    service = RuntimeSettingsService()
    snapshot = service.public_snapshot()

    assert snapshot["smtp"]["source"] == "env"
    assert snapshot["smtp"]["smtp_host"] == "smtp.env.local"
    assert snapshot["smtp"]["smtp_password_present"] is True
    assert snapshot["smtp"]["alerting_ready"] is True
    assert "smtp_password" not in snapshot["smtp"]

    assert snapshot["github"]["source"] == "env"
    assert snapshot["github"]["github_data_share_raw_base_url_effective"] == (
        "https://raw.githubusercontent.com/env-owner/env-repo/release"
    )
    assert snapshot["github"]["raw_archive_repo_url"] == "git@github.com:env-owner/raw-archive.git"
    assert snapshot["github"]["raw_archive_branch"] == "main"

    assert snapshot["cloudflare"]["source"] == "env"
    assert snapshot["cloudflare"]["cloudflare_configured"] is True
    assert snapshot["site"]["public_ga4_measurement_id"] == "G-8P39XV52NC"
    assert snapshot["site"]["ga4_configured"] is True
    assert not (tmp_path / "system-settings.json").exists()


def test_updates_persist_and_reset_to_env(monkeypatch, tmp_path):
    cfg = _fake_config(tmp_path)
    cfg.automation.smtp_password = ""
    cfg.automation.admin_emails_raw = ""
    cfg.cloudflare_api_token = ""
    cfg.cloudflare_account_id = ""
    monkeypatch.setattr("src.services.settings_service.get_config", lambda: cfg)

    service = RuntimeSettingsService()

    service.update_smtp(
        {
            "smtp_host": "smtp.local.override",
            "smtp_port": 465,
            "smtp_username": "local-user",
            "smtp_password": "local-secret",
            "smtp_from_email": "local-alerts@example.com",
            "smtp_use_tls": False,
            "admin_emails_raw": "ops@example.com,dev@example.com",
        }
    )
    smtp_snapshot = service.public_snapshot()["smtp"]
    assert smtp_snapshot["source"] == "local"
    assert smtp_snapshot["smtp_host"] == "smtp.local.override"
    assert smtp_snapshot["smtp_port"] == 465
    assert smtp_snapshot["smtp_password_present"] is True
    assert smtp_snapshot["admin_emails"] == ["ops@example.com", "dev@example.com"]
    assert smtp_snapshot["alerting_ready"] is True

    settings_file = tmp_path / "system-settings.json"
    stored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert stored["smtp"]["smtp_password"] == "local-secret"
    assert settings_file.stat().st_mode & 0o777 == 0o600

    service.update_smtp(
        {
            "smtp_password": "   ",
            "smtp_from_email": "local-updated@example.com",
        }
    )
    smtp_runtime = service.smtp_runtime()
    assert smtp_runtime["smtp_password"] == "local-secret"
    assert smtp_runtime["smtp_from_email"] == "local-updated@example.com"

    service.update_github(
        {
            "github_data_share_repo_url": "https://github.com/local-owner/local-repo.git",
            "github_data_share_repo_branch": "stable",
            "raw_archive_enabled": False,
            "raw_archive_repo_url": "git@github.com:local-owner/raw.git",
            "raw_archive_branch": "archive-v2",
            "default_github_remote": "upstream",
            "default_github_branch": "stable",
        }
    )
    github_snapshot = service.public_snapshot()["github"]
    assert github_snapshot["source"] == "local"
    assert github_snapshot["default_github_remote"] == "upstream"
    assert github_snapshot["github_data_share_raw_base_url_effective"] == (
        "https://raw.githubusercontent.com/local-owner/local-repo/stable"
    )
    assert github_snapshot["raw_archive_enabled"] is False
    assert github_snapshot["raw_archive_repo_url"] == "git@github.com:local-owner/raw.git"
    assert github_snapshot["raw_archive_branch"] == "archive-v2"

    service.update_cloudflare(
        {
            "cloudflare_api_token": "local-cf-token",
            "cloudflare_account_id": "local-account-id",
            "default_cloudflare_project_name": "globalid-prod",
        }
    )
    cloudflare_snapshot = service.public_snapshot()["cloudflare"]
    assert cloudflare_snapshot["source"] == "local"
    assert cloudflare_snapshot["cloudflare_configured"] is True
    assert cloudflare_snapshot["default_cloudflare_project_name"] == "globalid-prod"

    service.update_site({"public_ga4_measurement_id": "g-8p39xv52nc"})
    site_snapshot = service.public_snapshot()["site"]
    assert site_snapshot["source"] == "local"
    assert site_snapshot["public_ga4_measurement_id"] == "G-8P39XV52NC"
    assert service.site_runtime()["public_ga4_measurement_id"] == "G-8P39XV52NC"

    service.update_smtp({"clear_smtp_password": True})
    assert service.public_snapshot()["smtp"]["smtp_password_present"] is False
    service.update_cloudflare(
        {
            "clear_cloudflare_api_token": True,
            "clear_cloudflare_account_id": True,
        }
    )
    assert service.public_snapshot()["cloudflare"]["cloudflare_configured"] is False

    service.reset_section("smtp")
    reset_snapshot = service.public_snapshot()["smtp"]
    assert reset_snapshot["source"] == "env"
    assert reset_snapshot["smtp_host"] == "smtp.env.local"
    assert reset_snapshot["smtp_password_present"] is False
    assert reset_snapshot["alerting_ready"] is False


def test_site_settings_reject_invalid_ga4_measurement_id(monkeypatch, tmp_path):
    monkeypatch.setattr("src.services.settings_service.get_config", lambda: _fake_config(tmp_path))
    service = RuntimeSettingsService()

    with pytest.raises(ValueError, match="GA4 Measurement ID"):
        service.update_site({"public_ga4_measurement_id": "UA-OLD-FORMAT"})

    with pytest.raises(ValueError, match="Raw archive repository URL"):
        service.update_github({"raw_archive_enabled": True, "raw_archive_repo_url": ""})
