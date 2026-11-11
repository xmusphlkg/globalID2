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
    incremental_country_codes: list[str] | tuple[str, ...] | None = None,
    incremental_site: bool = False,
    country_scopes_exact: bool = False,
) -> list[str]:
    """Generate site assets and partitioned CSV/JSON/XLSX downloads locally."""

    command = [
        str(python_path),
        "scripts/generate_site_data.py",
        "--direct-download-url-base",
        download_url_base,
    ]
    for country_code in incremental_country_codes or ():
        normalized = str(country_code or "").strip().upper()
        if normalized:
            command.extend(["--incremental-country", normalized])
    if incremental_site:
        command.append("--incremental-site")
    if country_scopes_exact:
        command.append("--exact-country-scopes")
    return command


def build_update_situation_notices_command(*, python_path: Path) -> list[str]:
    """Export Situation data from the database; collection and AI run on their own schedule."""

    return [str(python_path), "scripts/run_situation_notices.py", "--export-only"]


def build_validate_situation_release_command(*, python_path: Path) -> list[str]:
    """Validate the built notice-based Situation pages and data."""

    return [str(python_path), "scripts/validate_situation_release.py"]

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


def build_split_papers_bundle_command(
    *,
    python_path: Path,
    dist_dir: Path,
    papers_dir: Path,
    main_origin: str,
    papers_origin: str,
    pages_file_budget: int,
    pages_file_warn: int,
    research_on_main: bool = False,
) -> list[str]:
    """Slim Astro dist for Pages and stage the papers R2 bundle."""

    return [
        str(python_path),
        "scripts/split_papers_bundle.py",
        "--dist-dir",
        str(dist_dir),
        "--papers-dir",
        str(papers_dir),
        "--main-origin",
        main_origin,
        "--papers-origin",
        papers_origin,
        "--pages-file-budget",
        str(pages_file_budget),
        "--pages-file-warn",
        str(pages_file_warn),
        *(["--research-on-main"] if research_on_main else []),
    ]


def build_publish_papers_r2_command(
    *,
    python_path: Path,
    source_dir: Path,
) -> list[str]:
    """Incrementally publish the papers bundle to Cloudflare R2."""

    return [
        str(python_path),
        "scripts/publish_papers_r2.py",
        "--source-dir",
        str(source_dir),
        "--skip-public-verify",
    ]


def build_split_reports_bundle_command(
    *,
    python_path: Path,
    dist_dir: Path,
    reports_dir: Path,
    main_origin: str,
    reports_origin: str,
    pages_file_budget: int,
    pages_file_warn: int,
) -> list[str]:
    return [
        str(python_path), "scripts/split_reports_bundle.py",
        "--dist-dir", str(dist_dir),
        "--reports-dir", str(reports_dir),
        "--main-origin", main_origin,
        "--reports-origin", reports_origin,
        "--pages-file-budget", str(pages_file_budget),
        "--pages-file-warn", str(pages_file_warn),
    ]


def build_publish_reports_r2_command(*, python_path: Path, source_dir: Path) -> list[str]:
    return [str(python_path), "scripts/publish_reports_r2.py", "--source-dir", str(source_dir), "--skip-public-verify"]


def build_verify_reports_host_command(*, python_path: Path, reports_dir: Path, reports_origin: str) -> list[str]:
    return [str(python_path), "scripts/verify_reports_host.py", "--source-dir", str(reports_dir), "--reports-origin", reports_origin]
