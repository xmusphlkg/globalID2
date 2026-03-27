"""Helpers for the dedicated download-data GitHub repository."""

from __future__ import annotations

import re

from .config import get_config

DEFAULT_DATA_SHARE_BRANCH = "main"
DEFAULT_LOCAL_DOWNLOAD_BASE_URL = "/downloads"

_GITHUB_REPO_PATTERNS = (
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
)


def get_data_share_repo_url() -> str:
    """Return the configured download-data repository URL."""
    cfg = get_config()
    return cfg.github_data_share_repo_url.strip()


def get_data_share_repo_branch(branch_override: str | None = None) -> str:
    """Return the configured target branch for the download-data repo."""
    if branch_override and branch_override.strip():
        return branch_override.strip()
    cfg = get_config()
    return cfg.github_data_share_repo_branch.strip() or DEFAULT_DATA_SHARE_BRANCH


def derive_github_raw_base_url(repo_url: str, branch: str) -> str:
    """Build a raw.githubusercontent.com base URL when the repo is hosted on GitHub."""
    normalized = (repo_url or "").strip()
    target_branch = (branch or "").strip() or DEFAULT_DATA_SHARE_BRANCH
    for pattern in _GITHUB_REPO_PATTERNS:
        match = pattern.match(normalized)
        if match:
            owner = match.group("owner")
            repo = match.group("repo")
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{target_branch}"
    return ""


def get_data_share_raw_base_url(
    repo_url: str | None = None,
    branch: str | None = None,
) -> str:
    """Return the configured or derived raw base URL for download artifacts."""
    cfg = get_config()
    configured = cfg.github_data_share_raw_base_url.strip()
    if configured:
        return configured.rstrip("/")
    target_repo = (repo_url or get_data_share_repo_url()).strip()
    target_branch = get_data_share_repo_branch(branch)
    derived = derive_github_raw_base_url(target_repo, target_branch).rstrip("/")
    return derived or DEFAULT_LOCAL_DOWNLOAD_BASE_URL
