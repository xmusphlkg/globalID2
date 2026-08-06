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
    download_url_base: str,
) -> list[str]:
    """Generate site assets and partitioned CSV/JSON/XLSX downloads locally."""

    return [
        str(python_path),
        "scripts/generate_site_data.py",
        "--direct-download-url-base",
        download_url_base,
    ]

def build_publish_download_repo_command(
    *,
    python_path: Path,
    source_dir: Path,
    repo_url: str,
    commit_message: str,
    branch: str = "main",
) -> list[str]:
    """Build the incremental, validated download publisher command."""

    return [
        str(python_path),
        "scripts/publish_download_repo.py",
        "--source-dir",
        str(source_dir),
        "--repo-url",
        repo_url,
        "--branch",
        branch,
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
