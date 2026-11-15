#!/usr/bin/env python3
"""Mirror ``data/raw`` into a configured branch of a dedicated Git repository.

Normal files keep their original paths and bytes so people can browse them on
GitHub. Files above GitHub's per-file limit are split into adjacent raw byte
parts and documented in ``.globalid-large-files.json``. Repeated runs commit
and push only actual changes; no pull request or GitHub Release is created.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.data_release.raw_archive import files as _files  # noqa: E402
from src.services.data_release.raw_archive import transport as _transport  # noqa: E402
from src.services.data_release.raw_archive.contracts import (  # noqa: E402
    ArchiveResult, LargeFileRecord, PartRecord, RawArchiveError, _SyncItem,
)
from src.services.data_release.raw_archive.repository import GitRepository  # noqa: E402

DEFAULT_SOURCE_DIR = ROOT / "data" / "raw"
DEFAULT_REPOSITORY_DIR = ROOT / "exports" / "raw-git-archive"
DEFAULT_REPO_URL = os.getenv("RAW_ARCHIVE__REPO_URL", "").strip()
TARGET_BRANCH = os.getenv("RAW_ARCHIVE__BRANCH", "main").strip() or "main"
DATA_ROOT = PurePosixPath("data/raw")
MARKER_FILE = ".globalid-raw-mirror"
MARKER_CONTENT = "globalid-raw-mirror-v2\n"
LARGE_FILES_INDEX = ".globalid-large-files.json"

# GitHub rejects individual Git objects above 100 MiB. Keep enough headroom
# for boundary interpretation while retaining nearly every source file as-is.
MAX_DIRECT_FILE_BYTES = 95 * 1024 * 1024
PART_BYTES = 48 * 1024 * 1024
# Multiple bounded commits make the first multi-gigabyte sync resumable. These
# are implementation details rather than user-facing tuning knobs.
COMMIT_BATCH_BYTES = 96 * 1024 * 1024
DEFAULT_GIT_TIMEOUT_SECONDS = 30 * 60
PUSH_ATTEMPTS = 4
PUSH_RETRY_DELAY_SECONDS = 2.0
GITHUB_SSH_PREFIXES = ("git@github.com:", "ssh://git@github.com/")

# These messages describe transport failures for which retrying the exact same
# fast-forward push is safe. Authentication, authorization and non-fast-forward
# failures are deliberately absent so operators receive those errors at once.
TRANSIENT_PUSH_ERROR_MARKERS = (
    "gnutls",
    "tls connection",
    "connection reset",
    "connection closed",
    "connection timed out",
    "could not resolve host",
    "failed to connect",
    "remote end hung up unexpectedly",
    "unexpected disconnect",
    "http/2 stream",
    "http 502",
    "http 503",
    "http 504",
    "the requested url returned error: 502",
    "the requested url returned error: 503",
    "the requested url returned error: 504",
)


def _sha256_file(path: Path) -> str:
    return _files._sha256_file(path)


def _source_identity(path: Path) -> tuple[int, str]:
    return _files._source_identity(path, sha256_file=_sha256_file)


def _safe_relative_path(value: str) -> PurePosixPath:
    return _files._safe_relative_path(value)


def iter_source_files(source_dir: Path) -> Iterator[tuple[Path, Path]]:
    return _files.iter_source_files(source_dir)


def _git_environment() -> dict[str, str]:
    return _transport._git_environment()


def _is_github_ssh_repo(repo_url: str) -> bool:
    normalized = (repo_url or "").strip().casefold()
    return any(normalized.startswith(prefix) for prefix in GITHUB_SSH_PREFIXES)


def configure_github_ssh_transport(repo_url: str) -> bool:
    """Route GitHub SSH traffic over port 443 for restricted release networks."""

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


def run_git(args: list[str], cwd: Path, *, timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
            check: bool = True) -> subprocess.CompletedProcess[str]:
    return _transport.run_git(args, cwd, timeout_seconds=timeout_seconds,
        check=check, environment=_git_environment)


def _is_transient_push_error(exc: RawArchiveError) -> bool:
    detail = str(exc).casefold()
    return any(marker in detail for marker in TRANSIENT_PUSH_ERROR_MARKERS)


def _push_branch(repository_dir: Path, *, timeout_seconds: float,
                 attempts: int = PUSH_ATTEMPTS, retry_delay_seconds: float = PUSH_RETRY_DELAY_SECONDS) -> None:
    _transport._push_branch(repository_dir, timeout_seconds=timeout_seconds,
        attempts=attempts, retry_delay_seconds=retry_delay_seconds, run_git=run_git,
        branch=TARGET_BRANCH, is_transient_error=_is_transient_push_error, sleep=time.sleep)


def _repository() -> GitRepository:
    return GitRepository(run_git=run_git, push_branch=_push_branch, branch=TARGET_BRANCH, root=ROOT)


def _configure_git(repository_dir: Path, timeout_seconds: float) -> None:
    _repository().configure_git(repository_dir, timeout_seconds)


def _working_tree_has_tracked_changes(repository_dir: Path, timeout_seconds: float) -> bool:
    return _repository().working_tree_has_tracked_changes(repository_dir, timeout_seconds)


def _remote_branch_exists(repo_url: str, cwd: Path, timeout_seconds: float) -> bool:
    return _repository().remote_branch_exists(repo_url, cwd, timeout_seconds)


def _switch_existing_clone_to_main(repository_dir: Path, *, remote_has_main: bool,
                                   timeout_seconds: float) -> None:
    _repository().switch_existing_clone_to_main(repository_dir,
        remote_has_main=remote_has_main, timeout_seconds=timeout_seconds)


def _fast_forward_from_remote(repository_dir: Path, timeout_seconds: float) -> None:
    _repository().fast_forward_from_remote(repository_dir, timeout_seconds)


def _push_pending_commits(repository_dir: Path, timeout_seconds: float) -> None:
    _repository().push_pending_commits(repository_dir, timeout_seconds)


def _ensure_repository(repository_dir: Path, *, repo_url: str, push: bool,
                       timeout_seconds: float) -> None:
    _repository().ensure_repository(repository_dir, repo_url=repo_url,
        push=push, timeout_seconds=timeout_seconds)


def _atomic_copy(source: Path, destination: Path, *, size: int, sha256: str) -> None:
    _files._atomic_copy(source, destination, size=size, sha256=sha256, sha256_file=_sha256_file)


def _bounded_copies(workers: int):
    return _files._bounded_copies(workers, copy_file=_atomic_copy)


class _PartWriter(_files._PartWriter):
    def __init__(self, directory: Path, relative_prefix: PurePosixPath):
        super().__init__(directory, relative_prefix, part_bytes=PART_BYTES)


def _parts_are_current(repository_dir: Path, record: LargeFileRecord) -> bool:
    return _files._parts_are_current(repository_dir, record,
        sha256_file=_sha256_file, safe_relative_path=_safe_relative_path)


def _split_file(source: Path, repository_dir: Path, relative: PurePosixPath,
                *, size: int, sha256: str) -> LargeFileRecord:
    return _files._split_file(source, repository_dir, relative, size=size, sha256=sha256,
        data_root=DATA_ROOT, writer_factory=_PartWriter)


def _load_large_file_index(repository_dir: Path) -> dict[str, LargeFileRecord]:
    path = repository_dir / LARGE_FILES_INDEX
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = {}
        for item in payload.get("files", []):
            record = LargeFileRecord(
                path=str(item["path"]),
                size=int(item["size"]),
                sha256=str(item["sha256"]),
                parts=tuple(
                    PartRecord(
                        path=str(part["path"]),
                        size=int(part["size"]),
                        sha256=str(part["sha256"]),
                    )
                    for part in item["parts"]
                ),
            )
            _safe_relative_path(record.path)
            records[record.path] = record
        return records
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RawArchiveError(f"Invalid large-file index: {path}") from exc


def _write_text_if_changed(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _write_repository_metadata(
    repository_dir: Path, large_files: list[LargeFileRecord]
) -> None:
    _write_text_if_changed(repository_dir / MARKER_FILE, MARKER_CONTENT)
    _write_text_if_changed(
        repository_dir / "README.md",
        "# GlobalID raw data archive\n\n"
        f"本仓库由 GlobalID 自动维护，`{TARGET_BRANCH}` 分支直接镜像 `data/raw`。普通文件保留原始目录、"
        "文件名和内容，可以直接浏览或下载。同步任务只提交实际新增、更新或删除的数据；不创建 PR 或 Release。\n\n"
        f"This repository is maintained automatically by GlobalID. The `{TARGET_BRANCH}` branch mirrors "
        "`data/raw` with original paths and bytes. Authentication data under `_auth` "
        "and private model requests under `data/raw/ai` are excluded.\n\n"
        "## 超大文件 / Large files\n\n"
        "GitHub 不接受超过 100 MiB 的单个 Git 文件，因此极少数超大文件会保存为相邻的 "
        "`<原文件名>.parts/part-*`。它们未经压缩，按文件名顺序拼接即可恢复：\n\n"
        "```bash\n"
        "cat data/raw/path/to/file.ext.parts/part-* > file.ext\n"
        "```\n\n"
        f"`{LARGE_FILES_INDEX}` 记录这些文件的原始大小、SHA-256 和分片校验值。\n",
    )
    index_payload = {
        "format": "globalid.raw-mirror.large-files.v2",
        "part_bytes": PART_BYTES,
        "files": [asdict(record) for record in sorted(large_files, key=lambda item: item.path)],
    }
    _write_text_if_changed(
        repository_dir / LARGE_FILES_INDEX,
        json.dumps(index_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _remove_stale_storage(
    repository_dir: Path,
    expected_files: set[str],
    *,
    timeout_seconds: float,
) -> tuple[list[str], int]:
    data_root = repository_dir.joinpath(*DATA_ROOT.parts)
    removed: set[str] = set()
    if data_root.exists():
        for candidate in sorted(data_root.rglob("*"), reverse=True):
            relative = candidate.relative_to(repository_dir).as_posix()
            if candidate.is_symlink() or (candidate.is_file() and relative not in expected_files):
                candidate.unlink()
                removed.add(relative)
            elif candidate.is_dir():
                try:
                    candidate.rmdir()
                except OSError:
                    pass
    # A previous run may have removed a stale file and stopped before staging
    # it. Include tracked-but-now-missing paths so the next run finishes that
    # deletion instead of leaving a permanently dirty clone.
    tracked = run_git(
        ["ls-files", "--", DATA_ROOT.as_posix()],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    removed.update(
        path for path in tracked.stdout.splitlines() if path and path not in expected_files
    )
    paths = sorted(removed)
    return paths, len(paths)


def _commit_paths(
    repository_dir: Path,
    paths: list[str],
    message: str,
    *,
    push: bool,
    timeout_seconds: float,
) -> bool:
    if not paths:
        return False
    unique_paths: list[str] = []
    for path in dict.fromkeys(paths):
        if (repository_dir / path).exists():
            unique_paths.append(path)
            continue
        tracked = run_git(
            ["ls-files", "--", path],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
        if tracked.stdout.strip():
            unique_paths.append(path)
    if not unique_paths:
        return False
    run_git(["add", "-A", "--", *unique_paths], repository_dir, timeout_seconds=timeout_seconds)
    staged = run_git(
        ["diff", "--cached", "--quiet"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        raise RawArchiveError("Unable to inspect staged archive changes")
    run_git(
        ["commit", "--no-gpg-sign", "-m", message],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    if push:
        _push_branch(
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
    return True


def _commit_sync_items(
    repository_dir: Path,
    items: list[_SyncItem],
    *,
    push: bool,
    timeout_seconds: float,
) -> int:
    commits = 0
    batch_paths: list[str] = []
    batch_sources: list[str] = []
    batch_bytes = 0

    def flush() -> None:
        nonlocal commits, batch_paths, batch_sources, batch_bytes
        if batch_paths and _commit_paths(
            repository_dir,
            batch_paths,
            f"data: sync {len(batch_sources)} raw file(s)",
            push=push,
            timeout_seconds=timeout_seconds,
        ):
            commits += 1
        batch_paths = []
        batch_sources = []
        batch_bytes = 0

    for item in items:
        if batch_paths and batch_bytes + item.stored_bytes > COMMIT_BATCH_BYTES:
            flush()
        batch_paths.extend(item.storage_paths)
        batch_sources.append(item.source_path)
        batch_bytes += item.stored_bytes
    flush()
    return commits


def publish_raw_archive(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    repository_dir: Path = DEFAULT_REPOSITORY_DIR,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    push: bool = False,
    git_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    copy_workers: int = 8,
) -> ArchiveResult:
    if not 1 <= copy_workers <= 16:
        raise ValueError("Raw archive copy workers must be between 1 and 16")
    source_root = Path(source_dir).resolve()
    repository_root = Path(repository_dir).resolve()
    try:
        source_root.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise RawArchiveError("Source directory cannot be inside the archive repository")
    try:
        repository_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise RawArchiveError("Archive repository cannot be inside the source directory")

    lock_path = repository_root.parent / f".{repository_root.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
                raise RawArchiveError(f"Another raw archive publisher holds {lock_path}") from exc

        if push:
            configure_github_ssh_transport(repo_url)
        _ensure_repository(
            repository_root,
            repo_url=repo_url.strip(),
            push=push,
            timeout_seconds=git_timeout_seconds,
        )
        previous_large_files = _load_large_file_index(repository_root)

        source_entries = list(iter_source_files(source_root))
        expected_storage_files: set[str] = set()
        sync_items: list[_SyncItem] = []
        large_files: list[LargeFileRecord] = []
        source_bytes = 0
        added = 0
        updated = 0
        changed_progress_events = 0

        def report_change(action: str, index: int, relative: PurePosixPath, size: int) -> None:
            nonlocal changed_progress_events
            changed_progress_events += 1
            if changed_progress_events <= 3 or changed_progress_events % 100 == 0:
                print(
                    f"{action} change {changed_progress_events} "
                    f"(source {index}/{len(source_entries)}): {relative} ({size} bytes)",
                    flush=True,
                )

        print(f"Raw archive copy workers: {copy_workers}", flush=True)
        with _bounded_copies(copy_workers) as copy:
            for index, (relative_path, source_path) in enumerate(source_entries, start=1):
                relative = PurePosixPath(relative_path.as_posix())
                size, digest = _source_identity(source_path)
                source_bytes += size
                direct_relative = DATA_ROOT / relative
                direct_destination = repository_root.joinpath(*direct_relative.parts)
                split_relative = PurePosixPath(f"{direct_relative.as_posix()}.parts")
                split_destination = repository_root.joinpath(*split_relative.parts)

                if size <= MAX_DIRECT_FILE_BYTES:
                    copy_future = None
                    existed = direct_destination.exists() or split_destination.exists()
                    current = (
                        direct_destination.is_file()
                        and direct_destination.stat().st_size == size
                        and _sha256_file(direct_destination) == digest
                    )
                    if not current:
                        report_change("copy", index, relative, size)
                        copy_future = copy(source_path, direct_destination, size=size, sha256=digest)
                        if existed:
                            updated += 1
                        else:
                            added += 1
                        sync_items.append(
                            _SyncItem(
                                source_path=relative.as_posix(),
                                storage_paths=(direct_relative.as_posix(),),
                                stored_bytes=size,
                            )
                        )
                    if split_destination.exists():
                        # Preserve the previous split representation if its
                        # replacement fails verification (large -> small).
                        if copy_future is not None:
                            copy_future.result()
                        shutil.rmtree(split_destination)
                        sync_items.append(
                            _SyncItem(
                                source_path=f"{relative.as_posix()} (obsolete parts)",
                                storage_paths=(split_relative.as_posix(),),
                                stored_bytes=0,
                            )
                        )
                    expected_storage_files.add(direct_relative.as_posix())
                    continue

                previous = previous_large_files.get(relative.as_posix())
                current = (
                    previous is not None
                    and previous.size == size
                    and previous.sha256 == digest
                    and _parts_are_current(repository_root, previous)
                )
                existed = direct_destination.exists() or split_destination.exists()
                if current:
                    record = previous
                else:
                    report_change("split", index, relative, size)
                    record = _split_file(
                        source_path,
                        repository_root,
                        relative,
                        size=size,
                        sha256=digest,
                    )
                    if existed:
                        updated += 1
                    else:
                        added += 1
                large_files.append(record)
                part_paths = tuple(part.path for part in record.parts)
                expected_storage_files.update(part_paths)
                if not current:
                    for part in record.parts:
                        sync_items.append(
                            _SyncItem(
                                source_path=f"{relative.as_posix()} ({part.path})",
                                storage_paths=(part.path,),
                                stored_bytes=part.size,
                            )
                        )
                if direct_destination.exists():
                    direct_destination.unlink()
                    sync_items.append(
                        _SyncItem(
                            source_path=f"{relative.as_posix()} (obsolete direct file)",
                            storage_paths=(direct_relative.as_posix(),),
                            stored_bytes=0,
                        )
                    )
                if previous is not None:
                    obsolete_parts = sorted(
                        {part.path for part in previous.parts} - set(part_paths)
                    )
                    if obsolete_parts:
                        sync_items.append(
                            _SyncItem(
                                source_path=f"{relative.as_posix()} (obsolete parts)",
                                storage_paths=tuple(obsolete_parts),
                                stored_bytes=0,
                            )
                        )

        # A previous run may have copied a file before it was interrupted.
        # Its bytes already match, but Git still needs to commit it.
        pending_paths: set[str] = set()
        for args in (
            ["ls-files", "--others", "--exclude-standard", "-z", "--", DATA_ROOT.as_posix()],
            ["diff", "--name-only", "-z", "--", DATA_ROOT.as_posix()],
            ["diff", "--cached", "--name-only", "-z", "--", DATA_ROOT.as_posix()],
        ):
            result = run_git(args, repository_root, timeout_seconds=git_timeout_seconds)
            pending_paths.update(path for path in result.stdout.split("\0") if path)
        already_scheduled = {
            path for item in sync_items for path in item.storage_paths
        }
        for path in sorted((pending_paths & expected_storage_files) - already_scheduled):
            sync_items.append(_SyncItem(source_path=path, storage_paths=(path,), stored_bytes=0))

        stale_paths, removed_count = _remove_stale_storage(
            repository_root,
            expected_storage_files,
            timeout_seconds=git_timeout_seconds,
        )
        if stale_paths:
            sync_items.append(
                _SyncItem(
                    source_path="<removed paths>",
                    storage_paths=tuple(stale_paths),
                    stored_bytes=0,
                )
            )

        _write_repository_metadata(repository_root, large_files)
        commit_count = _commit_sync_items(
            repository_root,
            sync_items,
            push=push,
            timeout_seconds=git_timeout_seconds,
        )
        if _commit_paths(
            repository_root,
            [MARKER_FILE, "README.md", LARGE_FILES_INDEX],
            "docs: update raw archive metadata",
            push=push,
            timeout_seconds=git_timeout_seconds,
        ):
            commit_count += 1

        changed = commit_count > 0
        return ArchiveResult(
            mode="pushed" if changed and push else ("local" if changed else "unchanged"),
            branch=TARGET_BRANCH,
            source_file_count=len(source_entries),
            source_bytes=source_bytes,
            added_file_count=added,
            updated_file_count=updated,
            removed_storage_path_count=removed_count,
            split_file_count=len(large_files),
            commit_count=commit_count,
            changed=changed,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--repository-dir", type=Path, default=DEFAULT_REPOSITORY_DIR)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--copy-workers", type=int,
                        default=int(os.environ.get("RAW_ARCHIVE_COPY_WORKERS", "8")),
                        help="Concurrent verified atomic copies (1–16; default 8).")
    parser.add_argument(
        "--git-timeout-seconds", type=float, default=DEFAULT_GIT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit changes and push them directly to the configured remote branch.",
    )
    # A running worker may still have the old command builder in memory until
    # its next planned service restart. Accept and ignore those obsolete
    # options so automatic publication keeps working during that transition.
    parser.add_argument("--chunk-mib", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--commit-batch-mib", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--zstd-level", type=int, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    result = publish_raw_archive(
        args.source_dir,
        args.repository_dir,
        repo_url=args.repo_url,
        push=args.push,
        git_timeout_seconds=args.git_timeout_seconds,
        copy_workers=args.copy_workers,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
