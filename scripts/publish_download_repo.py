#!/usr/bin/env python3
"""Incrementally publish partitioned downloads to the data repository."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.data_share import (  # noqa: E402
    get_data_share_repo_branch,
    get_data_share_repo_url,
)

DEFAULT_SOURCE_DIR = ROOT / "exports" / "site-downloads"
DEFAULT_WORKDIR = ROOT / "external-data" / "globalID2_data_download"
DEFAULT_REPO_URL = get_data_share_repo_url()
DEFAULT_REPO_BRANCH = get_data_share_repo_branch()
MANAGED_PATHS = ("countries", "diseases", "manifest.json")
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024
ASSET_DIRECTORIES = ("countries", "diseases")


def files_match(source_path: Path, target_path: Path) -> bool:
    """Compare file contents without relying on filecmp's stat-based cache."""

    if source_path.stat().st_size != target_path.stat().st_size:
        return False
    with source_path.open("rb") as source_file, target_path.open("rb") as target_file:
        while source_chunk := source_file.read(1024 * 1024):
            if source_chunk != target_file.read(len(source_chunk)):
                return False
        return not target_file.read(1)


def run_git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def remote_branch_exists(repo_url: str, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", repo_url, branch],
        check=True,
        text=True,
        capture_output=True,
    )
    return bool(completed.stdout.strip())


def _asset_relative_path(relative_path: str) -> Path:
    """Return a safe, managed download asset path from manifest metadata."""

    candidate = Path(relative_path)
    if (
        not relative_path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or not candidate.parts
        or candidate.parts[0] not in ASSET_DIRECTORIES
    ):
        raise RuntimeError(f"Unsafe manifest asset path: {relative_path!r}")
    return candidate


def validate_source(source_dir: Path, branch: str) -> dict:
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing generated manifest: {manifest_path}. "
            "Run scripts/generate_site_data.py first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 4:
        raise RuntimeError("Download manifest must use partition schema version 4")
    if manifest.get("formats") != ["csv", "json", "xlsx"]:
        raise RuntimeError("Download manifest must advertise CSV, JSON and XLSX")
    base = str(manifest.get("download_url_base") or "")
    if not base.startswith("https://raw.githubusercontent.com/") or not base.endswith(
        f"/{branch}"
    ):
        raise RuntimeError(
            f"Manifest Raw base must target branch {branch!r}; got {base!r}"
        )
    entries = list(manifest.get("countries") or []) + list(
        manifest.get("diseases") or []
    )
    if not entries:
        raise RuntimeError("Refusing to publish an empty download manifest")
    declared_paths: set[Path] = set()
    for entry in entries:
        for part in entry.get("parts") or []:
            files = part.get("files") or {}
            for format_name in ("csv", "json", "xlsx"):
                file_meta = files.get(format_name) or {}
                relative = str(file_meta.get("relative_path") or "")
                public_url = str(file_meta.get("url") or "")
                asset_path = _asset_relative_path(relative)
                if asset_path in declared_paths:
                    raise RuntimeError(f"Manifest declares the same asset twice: {relative}")
                declared_paths.add(asset_path)
                file_path = source_dir / asset_path
                if not file_path.is_file():
                    raise FileNotFoundError(
                        f"Missing {format_name.upper()} asset for "
                        f"{entry.get('id')}/{part.get('id')}: {file_path}"
                    )
                if public_url != f"{base}/{asset_path.as_posix()}":
                    raise RuntimeError(
                        f"Public URL does not match generated file: {public_url!r}"
                    )
                size = file_path.stat().st_size
                if size != int(file_meta.get("bytes") or -1):
                    raise RuntimeError(f"Manifest size mismatch: {file_path}")
                if size >= GITHUB_MAX_FILE_BYTES:
                    raise RuntimeError(
                        f"GitHub rejects files of 100 MiB or more: {file_path} ({size} bytes)"
                    )
                digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                if digest != file_meta.get("sha256"):
                    raise RuntimeError(f"Manifest checksum mismatch: {file_path}")

    actual_paths = {
        path.relative_to(source_dir)
        for directory in ASSET_DIRECTORIES
        for path in (source_dir / directory).rglob("*")
        if path.is_file()
    }
    unexpected_paths = sorted(actual_paths - declared_paths)
    if unexpected_paths:
        displayed = ", ".join(path.as_posix() for path in unexpected_paths[:10])
        suffix = "" if len(unexpected_paths) <= 10 else f" (+{len(unexpected_paths) - 10} more)"
        raise RuntimeError(
            "Generated download assets are not declared by manifest.json: "
            f"{displayed}{suffix}"
        )
    return manifest


def ensure_repo(repo_url: str, branch: str, workdir: Path) -> None:
    checkout_ready = False
    if (workdir / ".git").exists():
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=workdir,
            text=True,
            capture_output=True,
        )
        checkout_ready = completed.returncode == 0

    if not checkout_ready:
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.parent.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["git", "clone", "--depth", "1", "--single-branch"]
        if remote_branch_exists(repo_url, branch):
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([repo_url, str(workdir)])
        subprocess.run(clone_cmd, check=True, text=True)
        run_git(["checkout", "-B", branch], workdir)
        return

    if remote_branch_exists(repo_url, branch):
        # A long-lived publisher checkout may currently point at a different
        # data branch (for example, the old default ``main``).  Checking out
        # the requested branch from the fetched remote ref avoids an
        # impossible fast-forward when those histories diverge.
        remote_ref = f"refs/remotes/origin/{branch}"
        run_git(["fetch", "origin", f"{branch}:{remote_ref}", "--depth", "1"], workdir)
        run_git(["checkout", "-B", branch, f"origin/{branch}"], workdir)
    else:
        run_git(["checkout", "-B", branch], workdir)


def ensure_commit_identity(workdir: Path) -> dict[str, str]:
    """Ensure unattended releases have a repository-local commit identity.

    Fresh worker checkouts do not necessarily inherit a global Git identity.
    Prefer an explicitly supplied environment identity and otherwise reuse the
    author of the repository's current commit.  The latter keeps automated
    updates consistent with the repository that owns the generated assets
    without inventing a machine-wide identity.
    """

    def configured(key: str) -> str:
        completed = subprocess.run(
            ["git", "config", "--get", key],
            cwd=workdir,
            text=True,
            capture_output=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    name = configured("user.name")
    email = configured("user.email")
    if name and email:
        return {"name": name, "email": email, "source": "configured"}

    env_name = str(os.environ.get("GIT_AUTHOR_NAME") or "").strip()
    env_email = str(os.environ.get("GIT_AUTHOR_EMAIL") or "").strip()
    history = subprocess.run(
        ["git", "log", "-1", "--format=%an%x00%ae"],
        cwd=workdir,
        text=True,
        capture_output=True,
    )
    historical_name = ""
    historical_email = ""
    if history.returncode == 0 and "\x00" in history.stdout:
        historical_name, historical_email = (
            value.strip() for value in history.stdout.strip().split("\x00", 1)
        )

    resolved_name = name or env_name or historical_name
    resolved_email = email or env_email or historical_email
    if not resolved_name or not resolved_email:
        raise RuntimeError(
            "Git commit identity is not configured. Set GIT_AUTHOR_NAME and "
            "GIT_AUTHOR_EMAIL or configure user.name/user.email in the download repo."
        )
    if not name:
        run_git(["config", "--local", "user.name", resolved_name], workdir)
    if not email:
        run_git(["config", "--local", "user.email", resolved_email], workdir)
    return {
        "name": resolved_name,
        "email": resolved_email,
        "source": "environment" if env_name and env_email else "repository_history",
    }


def sync_managed_assets(source_dir: Path, workdir: Path) -> dict[str, int]:
    """Copy only changed files and remove partitions no longer in the manifest."""

    copied = 0
    removed = 0
    expected: set[Path] = set()
    for directory_name in ("countries", "diseases"):
        source_root = source_dir / directory_name
        target_root = workdir / directory_name
        if not source_root.is_dir():
            raise FileNotFoundError(f"Expected generated directory missing: {source_root}")
        for source_path in source_root.rglob("*"):
            if not source_path.is_file():
                continue
            relative = source_path.relative_to(source_dir)
            target_path = workdir / relative
            expected.add(target_path)
            if target_path.exists() and files_match(source_path, target_path):
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied += 1
        if target_root.exists():
            for target_path in sorted(target_root.rglob("*"), reverse=True):
                if target_path.is_file() and target_path not in expected:
                    target_path.unlink()
                    removed += 1
                elif target_path.is_dir() and not any(target_path.iterdir()):
                    target_path.rmdir()

    source_manifest = source_dir / "manifest.json"
    target_manifest = workdir / "manifest.json"
    if not target_manifest.exists() or not files_match(source_manifest, target_manifest):
        shutil.copy2(source_manifest, target_manifest)
        copied += 1
    return {"copied": copied, "removed": removed}


def write_readme(workdir: Path, manifest: dict) -> None:
    generated_at = manifest.get("generated_at") or datetime.now(timezone.utc).isoformat()
    countries = len(manifest.get("countries") or [])
    diseases = len(manifest.get("diseases") or [])
    base = manifest.get("download_url_base") or ""
    readme = f"""# GlobalID Data Downloads

This repository stores directly downloadable datasets for the GlobalID public site.

- Generated at: `{generated_at}`
- Country datasets: `{countries}`
- Disease datasets: `{diseases}`
- Formats: CSV, JSON and XLSX
- Partitioning: stable calendar windows; only changed windows are committed
- Manifest: [`manifest.json`](./manifest.json)

Public files are stable branch links under:

`{base}`
"""
    (workdir / "README.md").write_text(readme, encoding="utf-8")


def commit_and_push(
    workdir: Path,
    branch: str,
    commit_message: str,
    *,
    push: bool,
) -> bool:
    status = run_git(["status", "--short"], workdir)
    if not status:
        print("No changes to publish.")
        return False
    print(status)
    if not push:
        print("Validation complete. Pass --push to commit and publish these changes.")
        return False
    run_git(["add", "countries", "diseases", "manifest.json", "README.md"], workdir)
    run_git(["commit", "-m", commit_message], workdir)
    run_git(["push", "origin", branch], workdir)
    return True


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def verify_public_files(source_dir: Path, manifest: dict, commit_sha: str) -> None:
    """Compare representative remote Git blobs with the local generated files."""

    disease_entries = list(manifest.get("diseases") or [])
    representative = next(
        (entry for entry in disease_entries if entry.get("disease_id") == "D007"),
        disease_entries[0] if disease_entries else None,
    )
    if representative is None:
        raise RuntimeError("No disease dataset is available for public verification")

    raw_base = str(manifest["download_url_base"])
    raw_prefix = "https://raw.githubusercontent.com/"
    raw_parts = raw_base.removeprefix(raw_prefix).split("/")
    if not raw_base.startswith(raw_prefix) or len(raw_parts) != 3:
        raise RuntimeError(f"Unsupported GitHub Raw base: {raw_base}")
    owner, repository, _branch = raw_parts

    representative_parts = list(representative.get("parts") or [])
    if not representative_parts:
        raise RuntimeError("Representative disease has no downloadable partitions")
    current_part = next(
        (part for part in representative_parts if part.get("is_current")),
        representative_parts[0],
    )
    checks = [(source_dir / "manifest.json", "manifest.json")]
    for format_name in ("csv", "json", "xlsx"):
        relative_path = current_part["files"][format_name]["relative_path"]
        checks.append((source_dir / relative_path, relative_path))
    for local_path, relative_path in checks:
        completed = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{owner}/{repository}/contents/{relative_path}",
                "-f",
                f"ref={commit_sha}",
                "--jq",
                ".sha",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        remote_sha = completed.stdout.strip()
        local_sha = _git_blob_sha(local_path.read_bytes())
        if remote_sha != local_sha:
            raise RuntimeError(
                f"Published GitHub file differs from local output: {relative_path}"
            )
        print(f"Verified GitHub file: {raw_base}/{relative_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Incrementally publish partitioned CSV/JSON/XLSX assets"
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--branch", default=DEFAULT_REPO_BRANCH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--commit-message",
        default="chore: update partitioned data downloads",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push after validation (default is validation only)",
    )
    args = parser.parse_args()
    if not args.repo_url.strip():
        raise RuntimeError("Missing data download repository URL")

    manifest = validate_source(args.source_dir, args.branch)
    ensure_repo(args.repo_url, args.branch, args.workdir)
    identity = ensure_commit_identity(args.workdir)
    print(
        "Git commit identity: "
        f"{identity['name']} <{identity['email']}> ({identity['source']})"
    )
    sync_result = sync_managed_assets(args.source_dir, args.workdir)
    print(
        "Incremental sync: "
        f"{sync_result['copied']} changed files, {sync_result['removed']} removed files"
    )
    write_readme(args.workdir, manifest)
    pushed = commit_and_push(
        args.workdir,
        args.branch,
        args.commit_message,
        push=args.push,
    )
    if args.push:
        commit_sha = run_git(["rev-parse", "HEAD"], args.workdir)
        remote_sha = run_git(
            ["ls-remote", "origin", f"refs/heads/{args.branch}"],
            args.workdir,
        ).split()[0]
        if remote_sha != commit_sha:
            raise RuntimeError(
                f"Remote {args.branch} is {remote_sha}, expected pushed commit {commit_sha}"
            )
        verify_public_files(args.source_dir, manifest, commit_sha)
    if pushed:
        print(f"Published direct downloads to {args.repo_url} ({args.branch})")


if __name__ == "__main__":
    main()
