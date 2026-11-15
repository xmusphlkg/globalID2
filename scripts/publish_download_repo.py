#!/usr/bin/env python3
"""Incrementally publish partitioned downloads to the data repository."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.data_share import (  # noqa: E402
    get_data_share_repo_branch,
    get_data_share_repo_url,
)

from src.services.data_release.git_downloads import assets as _assets  # noqa: E402
from src.services.data_release.git_downloads import receipts as _receipts  # noqa: E402
from src.services.data_release.git_downloads import transport as _transport  # noqa: E402

DEFAULT_SOURCE_DIR = ROOT / "exports" / "site-downloads"
DEFAULT_WORKDIR = DEFAULT_SOURCE_DIR
DEFAULT_REPO_URL = get_data_share_repo_url()
DEFAULT_REPO_BRANCH = get_data_share_repo_branch()
MANAGED_PATHS = ("countries", "diseases", "manifest.json")
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024
PUSH_BATCH_BYTES = 1024 * 1024 * 1024
ASSET_DIRECTORIES = ("countries", "diseases")
GITHUB_SSH_PREFIXES = ("git@github.com:", "ssh://git@github.com/")
NETWORK_ATTEMPTS = 4
NETWORK_RETRY_DELAY_SECONDS = 2.0
GITHUB_VERIFY_ATTEMPTS = 6
GITHUB_VERIFY_RETRY_DELAY_SECONDS = 5.0
TRANSIENT_GIT_ERROR_MARKERS = (
    "gnutls",
    "tls connection",
    "connection reset",
    "connection timed out",
    "could not resolve host",
    "failed to connect",
    "connection closed",
    "remote end hung up unexpectedly",
    "unexpected disconnect",
    "http/2 stream",
    "the requested url returned error: 502",
    "the requested url returned error: 503",
    "the requested url returned error: 504",
)


def summarize_git_status(status: str, *, max_paths: int = 8) -> str:
    """Summarize a porcelain status without flooding task logs with every path."""

    lines = [line for line in status.splitlines() if line.strip()]
    if not lines:
        return "No changes to publish."
    counts = Counter((line[:2].strip() or "changed") for line in lines)
    count_summary = ", ".join(
        f"{status_code}: {count}" for status_code, count in sorted(counts.items())
    )
    displayed = lines[:max_paths]
    summary = [f"Git changes ready: {len(lines)} paths ({count_summary})"]
    summary.extend(f"  {line}" for line in displayed)
    omitted = len(lines) - len(displayed)
    if omitted:
        summary.append(f"  ... {omitted} additional paths omitted")
    return "\n".join(summary)


def files_match(source_path: Path, target_path: Path) -> bool:
    return _assets.files_match(source_path, target_path)


def run_git(args: list[str], cwd: Path) -> str:
    return _transport.run_git(args, cwd)


def _is_github_ssh_repo(repo_url: str) -> bool:
    normalized = (repo_url or "").strip().casefold()
    return any(normalized.startswith(prefix) for prefix in GITHUB_SSH_PREFIXES)


def configure_github_ssh_transport(repo_url: str) -> bool:
    """Route GitHub SSH traffic over port 443 for restricted networks.

    The release preflight can briefly succeed on port 22 and the later fetch
    can still time out. Configuring the publisher process itself avoids that
    race while retaining any identity/proxy options supplied by its parent.
    """

    if not _is_github_ssh_repo(repo_url):
        return False
    current = str(os.environ.get("GIT_SSH_COMMAND") or "ssh").strip()
    if "Hostname=ssh.github.com" not in current:
        current += " -o Hostname=ssh.github.com"
    if " -p 443" not in current and "Port=443" not in current:
        current += " -p 443"
    os.environ["GIT_SSH_COMMAND"] = current
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    return True


def _is_transient_git_error(exc: RuntimeError) -> bool:
    detail = str(exc).casefold()
    return any(marker in detail for marker in TRANSIENT_GIT_ERROR_MARKERS)


def run_git_with_retry(args: list[str], cwd: Path, *, attempts: int = NETWORK_ATTEMPTS,
                       retry_delay_seconds: float = NETWORK_RETRY_DELAY_SECONDS) -> str:
    return _transport.run_git_with_retry(args, cwd, run_git=run_git,
        is_transient_error=_is_transient_git_error, sleep=time.sleep,
        attempts=attempts, retry_delay_seconds=retry_delay_seconds)


def push_branch(
    workdir: Path,
    branch: str,
    *,
    attempts: int = 3,
    retry_delay_seconds: float = 2,
) -> None:
    """Retry bounded push failures while keeping Git's fast-forward safety intact."""

    run_git_with_retry(
        ["push", "origin", branch],
        workdir,
        attempts=attempts,
        retry_delay_seconds=retry_delay_seconds,
    )


def remote_branch_exists(repo_url: str, branch: str) -> bool:
    configure_github_ssh_transport(repo_url)
    output = run_git_with_retry(
        ["ls-remote", "--heads", repo_url, branch],
        ROOT,
    )
    return bool(output.strip())


def push_download_snapshot(workdir: Path, branch: str, *, batch_bytes: int = PUSH_BATCH_BYTES) -> None:
    _transport.push_download_snapshot(workdir, branch, batch_bytes=batch_bytes,
        run_git=run_git, run_git_with_retry=run_git_with_retry, push_branch=push_branch)


def _asset_relative_path(relative_path: str) -> Path:
    return _assets._asset_relative_path(relative_path, asset_directories=ASSET_DIRECTORIES)


def validate_source(source_dir: Path, branch: str) -> dict:
    return _assets.validate_source(source_dir, branch, parse_asset_path=_asset_relative_path,
        asset_directories=ASSET_DIRECTORIES, max_file_bytes=GITHUB_MAX_FILE_BYTES)


def ensure_repo(repo_url: str, branch: str, workdir: Path) -> None:
    configure_github_ssh_transport(repo_url)
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
        run_git_with_retry(
            ["fetch", "origin", f"{branch}:{remote_ref}", "--depth", "1"],
            workdir,
        )
        run_git(["checkout", "-B", branch, f"origin/{branch}"], workdir)
    else:
        run_git(["checkout", "-B", branch], workdir)


def prepare_export_repo(repo_url: str, branch: str, workdir: Path) -> None:
    """Adopt generated files in place; never reset their contents or local commits.

    Git history lives beside the export tree so artifact rollback restores only
    generated files. A first run fetches a baseline and resets the index only.
    """
    configure_github_ssh_transport(repo_url)
    workdir.mkdir(parents=True, exist_ok=True)
    metadata = workdir.parent / f".{workdir.name}.git"
    if not (workdir / ".git").exists():
        if metadata.is_dir():
            (workdir / ".git").write_text(f"gitdir: {metadata.resolve()}\n")
        else:
            run_git(["init", "--separate-git-dir", str(metadata.resolve()), "-b", branch, "."], workdir)
    remotes = run_git(["remote"], workdir).splitlines()
    if "origin" not in remotes:
        run_git(["remote", "add", "origin", repo_url], workdir)
    elif run_git(["remote", "get-url", "origin"], workdir) != repo_url:
        raise RuntimeError("Download repository destination changed; use a separate export directory")
    if run_git(["symbolic-ref", "--short", "HEAD"], workdir) != branch:
        raise RuntimeError("Download repository branch changed; use a separate export directory")
    # Artifact backups hard-link immutable exports, which changes ctime but
    # neither bytes nor mtime/inode. Keep those links from invalidating Git's
    # entire stat cache; atomic replacement still changes the inode/mtime.
    run_git(["config", "--local", "core.trustctime", "false"], workdir)
    head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=workdir, capture_output=True)
    if head.returncode != 0 and remote_branch_exists(repo_url, branch):
        run_git_with_retry(["fetch", "origin", f"{branch}:refs/remotes/origin/{branch}", "--depth", "1"], workdir)
        run_git(["reset", "--mixed", "--no-refresh", f"origin/{branch}"], workdir)


def _receipt_path(workdir: Path) -> Path:
    return _receipts._receipt_path(workdir, run_git=run_git)


def publication_is_verified(workdir: Path, repo_url: str, branch: str) -> bool:
    return _receipts.publication_is_verified(workdir, repo_url, branch,
        run_git=run_git, receipt_path=_receipt_path)


def record_publication(workdir: Path, repo_url: str, branch: str, commit: str) -> None:
    _receipts.record_publication(workdir, repo_url, branch, commit, receipt_path=_receipt_path)


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


def _manifest_assets(manifest_path: Path) -> dict[str, tuple[int, str]]:
    return _assets._manifest_assets(manifest_path, asset_path=_asset_relative_path)


def sync_managed_assets(source_dir: Path, workdir: Path) -> dict[str, int]:
    return _assets.sync_managed_assets(source_dir, workdir, manifest_assets=_manifest_assets,
        compare_files=files_match, sha256_file=_sha256_file)


def write_readme(workdir: Path, manifest: dict) -> None:
    _assets.write_readme(workdir, manifest)


def commit_and_push(
    workdir: Path,
    branch: str,
    commit_message: str,
    *,
    push: bool,
) -> bool:
    status = run_git(["status", "--short"], workdir)
    if not status:
        if push:
            # A clean tree can still contain a commit whose push failed.
            push_download_snapshot(workdir, branch)
            return True
        print("No changes to publish.")
        return False
    print(summarize_git_status(status))
    if not push:
        print("Validation complete. Pass --push to commit and publish these changes.")
        return False
    run_git(["add", "countries", "diseases", "manifest.json", "README.md"], workdir)
    run_git(["commit", "-m", commit_message], workdir)
    push_download_snapshot(workdir, branch)
    return True


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _assets._sha256_file(path)


def _read_remote_blob_sha(
    owner: str,
    repository: str,
    relative_path: str,
    commit_sha: str,
) -> str:
    command = [
        "gh",
        "api",
        "--method",
        "GET",
        f"repos/{owner}/{repository}/contents/{relative_path}",
        "-f",
        f"ref={commit_sha}",
        "--jq",
        ".sha",
    ]
    last_error = ""
    for attempt in range(1, GITHUB_VERIFY_ATTEMPTS + 1):
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
        last_error = "\n".join(
            part.strip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )
        if attempt < GITHUB_VERIFY_ATTEMPTS:
            print(
                f"GitHub verification attempt {attempt}/{GITHUB_VERIFY_ATTEMPTS} "
                f"failed for {relative_path}; retrying in "
                f"{GITHUB_VERIFY_RETRY_DELAY_SECONDS:g} seconds.",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(GITHUB_VERIFY_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        "GitHub verification failed for "
        f"{relative_path} at {commit_sha} after {GITHUB_VERIFY_ATTEMPTS} attempts"
        + (f"\n{last_error}" if last_error else "")
    )


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
        remote_sha = _read_remote_blob_sha(owner, repository, relative_path, commit_sha)
        local_sha = (
            run_git(["rev-parse", f"{commit_sha}:{relative_path}"], source_dir)
            if (source_dir / ".git").exists()
            else _git_blob_sha(local_path.read_bytes())
        )
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
    parser.add_argument("--workdir", type=Path, help="Separate legacy checkout (default: export directly in source-dir)")
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
    parser.add_argument(
        "--verify-checksums",
        action="store_true",
        help="Recompute every asset SHA-256 before publishing (full audit; reads all generated data)",
    )
    parser.add_argument("--verify-remote", action="store_true", help="Audit the remote even when the last verified local version is unchanged")
    args = parser.parse_args()
    if not args.repo_url.strip():
        raise RuntimeError("Missing data download repository URL")
    args.source_dir = args.source_dir.resolve()
    args.workdir = (args.workdir or args.source_dir).resolve()
    args.workdir.parent.mkdir(parents=True, exist_ok=True)
    with (args.workdir.parent / f".{args.workdir.name}.publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        publish(args)


def publish(args) -> None:
    in_place = args.source_dir == args.workdir
    if in_place:
        prepare_export_repo(args.repo_url, args.branch, args.workdir)
        if not args.verify_checksums and not args.verify_remote and publication_is_verified(
            args.workdir, args.repo_url, args.branch
        ):
            print("No download changes since verified publication; skipped validation, upload and remote checks.", flush=True)
            return

    phase_started = time.perf_counter()
    manifest = validate_source(args.source_dir, args.branch)
    print(f"Download validation completed in {time.perf_counter() - phase_started:.1f}s", flush=True)
    if args.verify_checksums:
        for asset_path, (_size, expected) in _manifest_assets(
            args.source_dir / "manifest.json"
        ).items():
            path = args.source_dir / asset_path
            if _sha256_file(path) != expected:
                raise RuntimeError(f"Manifest checksum mismatch: {path}")
    phase_started = time.perf_counter()
    if not in_place:
        if (args.workdir / ".git").is_file():
            # Frozen surveillance candidates share the persistent export Git
            # worktree. Preserve its local commits/index and generated files;
            # the push checks the remote fast-forward boundary once needed.
            prepare_export_repo(args.repo_url, args.branch, args.workdir)
        else:
            ensure_repo(args.repo_url, args.branch, args.workdir)
    print(f"Download repository sync completed in {time.perf_counter() - phase_started:.1f}s", flush=True)
    identity = ensure_commit_identity(args.workdir)
    print(
        "Git commit identity: "
        f"{identity['name']} <{identity['email']}> ({identity['source']})"
    )
    phase_started = time.perf_counter()
    sync_result = {"copied": 0, "removed": 0} if in_place else sync_managed_assets(args.source_dir, args.workdir)
    if (in_place or (args.workdir / ".git").is_file()) and not args.verify_checksums:
        # Check new/modified files only; Git already knows unchanged blobs.
        from src.generation.direct_download_files import _git_unchanged_paths

        trusted = _git_unchanged_paths(args.workdir)
        for relative, (_size, expected) in _manifest_assets(args.source_dir / "manifest.json").items():
            if relative not in trusted and _sha256_file(args.workdir / relative) != expected:
                raise RuntimeError(f"Manifest checksum mismatch: {relative}")
    if in_place:
        print(f"Using export Git worktree directly; changed-file validation completed in {time.perf_counter() - phase_started:.1f}s", flush=True)
    else:
        print(
            "Incremental sync: "
            f"{sync_result['copied']} changed files, {sync_result['removed']} removed files; "
            f"completed in {time.perf_counter() - phase_started:.1f}s",
            flush=True,
        )
    write_readme(args.workdir, manifest)
    phase_started = time.perf_counter()
    pushed = commit_and_push(
        args.workdir,
        args.branch,
        args.commit_message,
        push=args.push,
    )
    print(f"Download git commit/push completed in {time.perf_counter() - phase_started:.1f}s", flush=True)
    if args.push:
        phase_started = time.perf_counter()
        commit_sha = run_git(["rev-parse", "HEAD"], args.workdir)
        if args.verify_remote:
            remote_refs = run_git_with_retry(
                ["ls-remote", "origin", f"refs/heads/{args.branch}"],
                args.workdir,
            ).split()
            remote_sha = remote_refs[0] if remote_refs else "missing"
            if remote_sha != commit_sha:
                raise RuntimeError(
                    f"Remote {args.branch} is {remote_sha}, expected pushed commit {commit_sha}"
                )
            verify_public_files(args.source_dir, manifest, commit_sha)
        else:
            print("Public download verification skipped; Git push acknowledged.", flush=True)
        record_publication(args.workdir, args.repo_url, args.branch, commit_sha)
        print(f"Download publication recorded in {time.perf_counter() - phase_started:.1f}s", flush=True)
    if pushed:
        print(f"Published direct downloads to {args.repo_url} ({args.branch})")


if __name__ == "__main__":
    main()
