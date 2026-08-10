"""Runtime settings storage for SMTP, GitHub, and Cloudflare.

The service keeps a small JSON file under ``data/`` as the writable source of
truth, while environment variables remain the fallback defaults.  This lets the
dashboard manage shared settings without turning them into one-off page-local
state.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Optional
import re

from src.core import get_config, get_logger

logger = get_logger(__name__)

_DEFAULT_SETTINGS_FILENAME = "system-settings.json"
_GITHUB_REPO_PATTERNS = (
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _split_emails(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _derive_github_raw_base_url(repo_url: str, branch: str) -> str:
    normalized = (repo_url or "").strip()
    target_branch = (branch or "").strip() or "main"
    for pattern in _GITHUB_REPO_PATTERNS:
        match = pattern.match(normalized)
        if match:
            owner = match.group("owner")
            repo = match.group("repo")
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{target_branch}"
    return ""


class RuntimeSettingsService:
    """Read and write the small runtime settings file."""

    def __init__(self) -> None:
        self._lock = RLock()

    def _settings_path(self) -> Path:
        cfg = get_config()
        return cfg.data_dir / _DEFAULT_SETTINGS_FILENAME

    def _default_raw_snapshot(self) -> dict[str, dict[str, Any]]:
        cfg = get_config()
        automation = cfg.automation
        data_release = cfg.data_release
        return {
            "smtp": {
                "smtp_host": automation.smtp_host.strip(),
                "smtp_port": _coerce_int(automation.smtp_port, 587),
                "smtp_username": automation.smtp_username.strip(),
                "smtp_password": automation.smtp_password,
                "smtp_from_email": automation.smtp_from_email.strip(),
                "smtp_use_tls": bool(automation.smtp_use_tls),
                "admin_emails_raw": automation.admin_emails_raw.strip(),
            },
            "github": {
                "github_data_share_repo_url": cfg.github_data_share_repo_url.strip(),
                "github_data_share_repo_branch": cfg.github_data_share_repo_branch.strip(),
                "github_data_share_raw_base_url": cfg.github_data_share_raw_base_url.strip(),
                "raw_archive_enabled": bool(cfg.raw_archive.enabled),
                "raw_archive_repo_url": cfg.raw_archive.repo_url.strip(),
                "raw_archive_branch": str(getattr(cfg.raw_archive, "branch", "main") or "main").strip() or "main",
                "default_github_remote": data_release.default_github_remote.strip() or "origin",
                "default_github_branch": data_release.default_github_branch.strip(),
            },
            "cloudflare": {
                "cloudflare_api_token": cfg.cloudflare_api_token.strip(),
                "cloudflare_account_id": cfg.cloudflare_account_id.strip(),
                "default_cloudflare_project_name": data_release.default_cloudflare_project_name.strip() or "globalid",
            },
        }

    def _load_overrides(self) -> dict[str, Any]:
        path = self._settings_path()
        if not path.exists():
            return {}

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read runtime settings file %s: %s", path, exc)
            return {}

        return payload if isinstance(payload, dict) else {}

    def _write_overrides(self, overrides: dict[str, Any]) -> None:
        path = self._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _merge_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        defaults = self._default_raw_snapshot()
        overrides = self._load_overrides()
        merged = deepcopy(defaults)
        for section, values in overrides.items():
            if not isinstance(values, dict):
                continue
            current = dict(merged.get(section, {}))
            current.update(values)
            merged[section] = current
        return merged, overrides

    def _smtp_raw(self) -> dict[str, Any]:
        merged, _ = self._merge_snapshot()
        raw = merged.get("smtp", {})
        return {
            "smtp_host": str(raw.get("smtp_host") or "").strip(),
            "smtp_port": _coerce_int(raw.get("smtp_port"), 587),
            "smtp_username": str(raw.get("smtp_username") or "").strip(),
            "smtp_password": str(raw.get("smtp_password") or ""),
            "smtp_from_email": str(raw.get("smtp_from_email") or "").strip(),
            "smtp_use_tls": _coerce_bool(raw.get("smtp_use_tls"), True),
            "admin_emails_raw": str(raw.get("admin_emails_raw") or "").strip(),
        }

    def _github_raw(self) -> dict[str, Any]:
        merged, _ = self._merge_snapshot()
        raw = merged.get("github", {})
        return {
            "github_data_share_repo_url": str(raw.get("github_data_share_repo_url") or "").strip(),
            "github_data_share_repo_branch": str(raw.get("github_data_share_repo_branch") or "").strip(),
            "github_data_share_raw_base_url": str(raw.get("github_data_share_raw_base_url") or "").strip(),
            "raw_archive_enabled": _coerce_bool(raw.get("raw_archive_enabled"), False),
            "raw_archive_repo_url": str(raw.get("raw_archive_repo_url") or "").strip(),
            "raw_archive_branch": str(raw.get("raw_archive_branch") or "").strip() or "main",
            "default_github_remote": str(raw.get("default_github_remote") or "").strip() or "origin",
            "default_github_branch": str(raw.get("default_github_branch") or "").strip(),
        }

    def _cloudflare_raw(self) -> dict[str, Any]:
        merged, _ = self._merge_snapshot()
        raw = merged.get("cloudflare", {})
        return {
            "cloudflare_api_token": str(raw.get("cloudflare_api_token") or "").strip(),
            "cloudflare_account_id": str(raw.get("cloudflare_account_id") or "").strip(),
            "default_cloudflare_project_name": str(raw.get("default_cloudflare_project_name") or "").strip() or "globalid",
        }

    def smtp_runtime(self) -> dict[str, Any]:
        """Return the raw SMTP runtime settings, including the password."""
        return self._smtp_raw()

    def github_runtime(self) -> dict[str, Any]:
        return self._github_raw()

    def cloudflare_runtime(self) -> dict[str, Any]:
        return self._cloudflare_raw()

    def public_snapshot(self) -> dict[str, Any]:
        merged, overrides = self._merge_snapshot()
        smtp = self._build_smtp_public(merged.get("smtp", {}), source="local" if "smtp" in overrides else "env")
        github = self._build_github_public(merged.get("github", {}), source="local" if "github" in overrides else "env")
        cloudflare = self._build_cloudflare_public(
            merged.get("cloudflare", {}),
            source="local" if "cloudflare" in overrides else "env",
        )
        return {
            "smtp": smtp,
            "github": github,
            "cloudflare": cloudflare,
        }

    def _build_smtp_public(self, raw: dict[str, Any], *, source: str) -> dict[str, Any]:
        admin_emails_raw = str(raw.get("admin_emails_raw") or "").strip()
        admin_emails = _split_emails(admin_emails_raw)
        smtp_host = str(raw.get("smtp_host") or "").strip()
        smtp_port = _coerce_int(raw.get("smtp_port"), 587)
        smtp_username = str(raw.get("smtp_username") or "").strip()
        smtp_from_email = str(raw.get("smtp_from_email") or "").strip()
        smtp_use_tls = _coerce_bool(raw.get("smtp_use_tls"), True)
        smtp_password_present = bool(str(raw.get("smtp_password") or "").strip())
        smtp_configured = bool(smtp_host and smtp_port and smtp_username and smtp_from_email and smtp_password_present)
        return {
            "source": source,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_username": smtp_username,
            "smtp_from_email": smtp_from_email,
            "smtp_use_tls": smtp_use_tls,
            "admin_emails_raw": admin_emails_raw,
            "admin_emails": admin_emails,
            "smtp_password_present": smtp_password_present,
            "smtp_configured": smtp_configured,
            "alerting_ready": smtp_configured and bool(admin_emails),
        }

    def _build_github_public(self, raw: dict[str, Any], *, source: str) -> dict[str, Any]:
        repo_url = str(raw.get("github_data_share_repo_url") or "").strip()
        repo_branch = str(raw.get("github_data_share_repo_branch") or "").strip()
        raw_base_url = str(raw.get("github_data_share_raw_base_url") or "").strip()
        raw_archive_enabled = _coerce_bool(raw.get("raw_archive_enabled"), False)
        raw_archive_repo_url = str(raw.get("raw_archive_repo_url") or "").strip()
        raw_archive_branch = str(raw.get("raw_archive_branch") or "").strip() or "main"
        default_remote = str(raw.get("default_github_remote") or "").strip() or "origin"
        default_branch = str(raw.get("default_github_branch") or "").strip()
        derived_raw_base_url = raw_base_url.rstrip("/") if raw_base_url else _derive_github_raw_base_url(repo_url, repo_branch)
        return {
            "source": source,
            "github_data_share_repo_url": repo_url,
            "github_data_share_repo_branch": repo_branch,
            "github_data_share_raw_base_url": raw_base_url,
            "github_data_share_raw_base_url_effective": derived_raw_base_url or "/downloads",
            "raw_archive_enabled": raw_archive_enabled,
            "raw_archive_repo_url": raw_archive_repo_url,
            "raw_archive_branch": raw_archive_branch,
            "raw_archive_configured": bool(raw_archive_repo_url),
            "default_github_remote": default_remote,
            "default_github_branch": default_branch,
            "github_configured": bool(repo_url or raw_base_url),
            "release_defaults_ready": bool(default_remote),
        }

    def _build_cloudflare_public(self, raw: dict[str, Any], *, source: str) -> dict[str, Any]:
        api_token_present = bool(str(raw.get("cloudflare_api_token") or "").strip())
        account_id_present = bool(str(raw.get("cloudflare_account_id") or "").strip())
        default_project_name = str(raw.get("default_cloudflare_project_name") or "").strip() or "globalid"
        return {
            "source": source,
            "cloudflare_api_token_present": api_token_present,
            "cloudflare_account_id_present": account_id_present,
            "default_cloudflare_project_name": default_project_name,
            "cloudflare_configured": bool(api_token_present and account_id_present and default_project_name),
        }

    def _update_section(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            overrides = self._load_overrides()
            section_overrides = dict(overrides.get(section, {}))
            for key, value in payload.items():
                if key.startswith("_"):
                    continue
                if value is None:
                    section_overrides.pop(key, None)
                    continue
                if isinstance(value, str):
                    normalized = value.strip()
                    if key in {"smtp_password", "cloudflare_api_token", "cloudflare_account_id"} and not normalized:
                        continue
                    section_overrides[key] = normalized
                    continue
                section_overrides[key] = value

            if section_overrides:
                overrides[section] = section_overrides
            else:
                overrides.pop(section, None)

            self._write_overrides(overrides)
        return self.public_snapshot()

    def update_smtp(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._update_section("smtp", payload)

    def update_github(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._update_section("github", payload)

    def update_cloudflare(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._update_section("cloudflare", payload)

    def reset_section(self, section: str) -> dict[str, Any]:
        with self._lock:
            overrides = self._load_overrides()
            if section in overrides:
                overrides.pop(section, None)
                self._write_overrides(overrides)
        return self.public_snapshot()

    def build_smtp_status(self) -> dict[str, Any]:
        """Convenience summary used by task-alert and automation views."""
        public = self.public_snapshot()["smtp"]
        return {
            "admin_emails": list(public["admin_emails"]),
            "smtp_configured": bool(public["smtp_configured"]),
            "alerting_ready": bool(public["alerting_ready"]),
        }


system_settings_service = RuntimeSettingsService()
