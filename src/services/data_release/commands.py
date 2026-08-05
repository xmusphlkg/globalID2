"""Pure command construction for data release subprocesses."""

from __future__ import annotations

from pathlib import Path


def build_cloudflare_deploy_command(
    *,
    project_name: str,
    branch: str,
    source_commit: str,
    commit_message: str,
    commit_dirty: bool,
) -> list[str]:
    command = [
        "npm",
        "exec",
        "--",
        "wrangler",
        "pages",
        "deploy",
        "dist",
        "--project-name",
        project_name,
        "--branch",
        branch,
        "--commit-message",
        commit_message,
        f"--commit-dirty={'true' if commit_dirty else 'false'}",
    ]
    if source_commit and source_commit != "unknown":
        command.extend(["--commit-hash", source_commit])
    return command


def build_generate_site_data_command(
    *,
    python_path: Path,
    snapshot_url_base: str,
) -> list[str]:
    """Build the canonical v2 package and bounded GitHub snapshot locally."""

    return [
        str(python_path),
        "scripts/generate_site_data.py",
        "--github-snapshot-url-base",
        snapshot_url_base,
    ]

def build_publish_github_snapshot_command(
    *,
    python_path: Path,
    snapshot_dir: Path,
    repo_url: str,
    commit_message: str,
) -> list[str]:
    return [
        str(python_path),
        "scripts/publish_github_snapshot_v2.py",
        "--snapshot-dir",
        str(snapshot_dir),
        "--repo-url",
        repo_url,
        "--commit-message",
        commit_message,
        "--push",
    ]


def build_publish_raw_archive_command(
    *,
    python_path: Path,
    source_dir: Path,
    repository_dir: Path,
    repo_url: str,
    git_timeout_seconds: int,
) -> list[str]:
    return [
        str(python_path),
        "scripts/publish_raw_git_archive.py",
        "--source-dir",
        str(source_dir),
        "--repository-dir",
        str(repository_dir),
        "--repo-url",
        repo_url,
        "--git-timeout-seconds",
        str(git_timeout_seconds),
        "--push",
    ]
