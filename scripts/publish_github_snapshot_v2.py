#!/usr/bin/env python3
"""Publish one validated canonical snapshot as a bounded orphan branch.

The command is dry-run by default.  Passing ``--push`` is the only operation
that creates a temporary Git repository or contacts a remote.  The target
branch is intentionally fixed to ``snapshot-v2`` and every pushed commit is an
orphan, so branch history never grows without bound.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.generation.github_data_snapshot import (  # noqa: E402
    LATEST_FILENAME,
    RELEASES_DIRNAME,
    SNAPSHOT_MARKER_FILENAME,
    GitHubSnapshotValidationResult,
    validate_github_snapshot,
)


DEFAULT_SNAPSHOT_DIR = ROOT / "exports" / "github-data-snapshot-v2"
DEFAULT_REPO_URL = os.getenv("GITHUB_DATA_SHARE_REPO_URL", "").strip()
TARGET_BRANCH = "snapshot-v2"
DEFAULT_COMMIT_MESSAGE = "chore(data): publish canonical snapshot v2"
DEFAULT_GIT_TIMEOUT_SECONDS = 300.0
_ALLOWED_ROOT_ENTRIES = frozenset(
    {
        SNAPSHOT_MARKER_FILENAME,
        LATEST_FILENAME,
        RELEASES_DIRNAME,
        "README.md",
    }
)
_REMOTE_OID_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


class SnapshotPublishError(RuntimeError):
    """Raised when a validated snapshot cannot be safely prepared or pushed."""


@dataclass(frozen=True)
class SnapshotPublishResult:
    """Machine-readable dry-run or push result."""

    mode: str
    branch: str
    snapshot_dir: str
    latest_release_id: str
    release_count: int
    file_count: int
    total_bytes: int
    commit_oid: str | None = None
    previous_remote_oid: str | None = None


def _validate_v2_only_tree(snapshot_dir: Path) -> GitHubSnapshotValidationResult:
    """Fully validate the snapshot and reject legacy export-shaped additions."""

    # This full recursive validator must run before even ls-remote. It verifies
    # every retained release, manifest, index, gzip object, hash, count, date
    # range, and GitHub file/tree health limit.
    result = validate_github_snapshot(snapshot_dir)
    root = Path(snapshot_dir)
    actual_entries = {path.name for path in root.iterdir()}
    if actual_entries != _ALLOWED_ROOT_ENTRIES:
        unexpected = sorted(actual_entries - _ALLOWED_ROOT_ENTRIES)
        missing = sorted(_ALLOWED_ROOT_ENTRIES - actual_entries)
        raise SnapshotPublishError(
            f"Snapshot root is not v2-only; unexpected={unexpected}, missing={missing}"
        )
    csv_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".csv"
    )
    if csv_files:
        raise SnapshotPublishError(
            "Legacy CSV files are forbidden in snapshot-v2: " + ", ".join(csv_files)
        )
    return result


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git_command(
    args: list[str],
    cwd: Path,
    *,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
) -> str:
    """Run one bounded, non-interactive Git command and return stdout."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            capture_output=True,
            env=_git_environment(),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotPublishError(
            f"git {' '.join(args)} timed out after {timeout_seconds:g} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise SnapshotPublishError(
            f"git {' '.join(args)} failed with exit code {completed.returncode}{suffix}"
        )
    return completed.stdout.strip()


def _copy_v2_snapshot(snapshot_dir: Path, repository_dir: Path) -> None:
    for name in sorted(_ALLOWED_ROOT_ENTRIES):
        source = snapshot_dir / name
        destination = repository_dir / name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def _prepare_orphan_commit(
    snapshot_dir: Path,
    repository_dir: Path,
    *,
    commit_message: str,
    timeout_seconds: float,
) -> str:
    repository_dir.mkdir()
    run_git_command(
        ["init", "--quiet"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    run_git_command(
        ["checkout", "--orphan", TARGET_BRANCH],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    run_git_command(
        ["config", "user.name", "GlobalID Snapshot Publisher"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    run_git_command(
        ["config", "user.email", "snapshot-publisher@globalid.invalid"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    _copy_v2_snapshot(snapshot_dir, repository_dir)
    run_git_command(
        ["add", "--all"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    run_git_command(
        ["commit", "--no-gpg-sign", "-m", commit_message],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    commit_oid = run_git_command(
        ["rev-parse", "HEAD"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    if not _REMOTE_OID_PATTERN.fullmatch(commit_oid):
        raise SnapshotPublishError(
            f"Temporary orphan commit returned an invalid OID: {commit_oid!r}"
        )
    return commit_oid.lower()


def _remote_branch_oid(
    repository_dir: Path,
    repo_url: str,
    *,
    timeout_seconds: float,
) -> str | None:
    output = run_git_command(
        ["ls-remote", "--heads", "--", repo_url, f"refs/heads/{TARGET_BRANCH}"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    if not output:
        return None
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2:
        raise SnapshotPublishError(
            f"Unexpected ls-remote response for {TARGET_BRANCH}: {output!r}"
        )
    oid, ref_name = rows[0]
    if ref_name != f"refs/heads/{TARGET_BRANCH}" or not _REMOTE_OID_PATTERN.fullmatch(
        oid
    ):
        raise SnapshotPublishError(
            f"Invalid remote branch response for {TARGET_BRANCH}: {output!r}"
        )
    return oid.lower()


def publish_github_snapshot_v2(
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    push: bool = False,
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    temporary_parent: Path | None = None,
) -> SnapshotPublishResult:
    """Validate and optionally force-with-lease an orphan snapshot-v2 commit."""

    snapshot_input = Path(snapshot_dir)
    # Validate the caller-provided path before resolving it so a symlink cannot
    # hide from the snapshot validator's explicit symlink rejection.
    validation = _validate_v2_only_tree(snapshot_input)
    snapshot_root = snapshot_input.resolve()
    base = {
        "branch": TARGET_BRANCH,
        "snapshot_dir": str(snapshot_root),
        "latest_release_id": validation.latest_release_id,
        "release_count": validation.release_count,
        "file_count": validation.file_count,
        "total_bytes": validation.total_bytes,
    }
    if not push:
        return SnapshotPublishResult(mode="dry-run", **base)

    normalized_repo_url = repo_url.strip()
    if not normalized_repo_url:
        raise SnapshotPublishError(
            "--push requires --repo-url or GITHUB_DATA_SHARE_REPO_URL"
        )
    if any(character in normalized_repo_url for character in "\x00\r\n"):
        raise SnapshotPublishError("repo_url cannot contain control characters")
    if not commit_message.strip():
        raise SnapshotPublishError("commit_message must be non-empty")

    temp_parent = Path(temporary_parent).resolve() if temporary_parent else None
    if temp_parent is not None:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="globalid-snapshot-v2-publish-",
        dir=temp_parent,
    ) as temporary:
        repository_dir = Path(temporary) / "repository"
        commit_oid = _prepare_orphan_commit(
            snapshot_root,
            repository_dir,
            commit_message=commit_message.strip(),
            timeout_seconds=timeout_seconds,
        )
        previous_oid = _remote_branch_oid(
            repository_dir,
            normalized_repo_url,
            timeout_seconds=timeout_seconds,
        )
        # An empty expected OID means the branch must still be absent. If a
        # concurrent publisher creates or updates it after ls-remote, Git
        # rejects this lease instead of overwriting that newer snapshot.
        lease = f"--force-with-lease=refs/heads/{TARGET_BRANCH}:{previous_oid or ''}"
        run_git_command(
            [
                "push",
                lease,
                "--",
                normalized_repo_url,
                f"HEAD:refs/heads/{TARGET_BRANCH}",
            ],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
    return SnapshotPublishResult(
        mode="pushed",
        commit_oid=commit_oid,
        previous_remote_oid=previous_oid,
        **base,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help="Validated v2 snapshot tree; defaults to exports/github-data-snapshot-v2.",
    )
    parser.add_argument(
        "--repo-url",
        default=DEFAULT_REPO_URL,
        help="Target Git repository URL (required only with --push).",
    )
    parser.add_argument(
        "--commit-message",
        default=DEFAULT_COMMIT_MESSAGE,
    )
    parser.add_argument(
        "--git-timeout-seconds",
        type=float,
        default=DEFAULT_GIT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Explicitly contact the remote and publish; omitted means dry-run.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    result = publish_github_snapshot_v2(
        args.snapshot_dir,
        repo_url=args.repo_url,
        push=args.push,
        commit_message=args.commit_message,
        timeout_seconds=args.git_timeout_seconds,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
