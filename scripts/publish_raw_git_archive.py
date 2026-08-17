#!/usr/bin/env python3
"""Mirror ``data/raw`` into a configured branch of a dedicated Git repository.

Normal files keep their original paths and bytes so people can browse them on
GitHub. Files above GitHub's per-file limit are split into adjacent raw byte
parts and documented in ``.globalid-large-files.json``. Repeated runs commit
and push only actual changes; no pull request or GitHub Release is created.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, Iterator


ROOT = Path(__file__).resolve().parents[1]
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

# These messages describe transport failures for which retrying the exact same
# fast-forward push is safe. Authentication, authorization and non-fast-forward
# failures are deliberately absent so operators receive those errors at once.
TRANSIENT_PUSH_ERROR_MARKERS = (
    "gnutls",
    "tls connection",
    "connection reset",
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


class RawArchiveError(RuntimeError):
    """Raised when the raw-data mirror cannot be updated safely."""


@dataclass(frozen=True)
class PartRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LargeFileRecord:
    path: str
    size: int
    sha256: str
    parts: tuple[PartRecord, ...]


@dataclass(frozen=True)
class ArchiveResult:
    mode: str
    branch: str
    source_file_count: int
    source_bytes: int
    added_file_count: int
    updated_file_count: int
    removed_storage_path_count: int
    split_file_count: int
    commit_count: int
    changed: bool


@dataclass(frozen=True)
class _SyncItem:
    source_path: str
    storage_paths: tuple[str, ...]
    stored_bytes: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_identity(path: Path) -> tuple[int, str]:
    before = path.stat()
    digest = _sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RawArchiveError(f"Source file changed while it was being read: {path}")
    return after.st_size, digest


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RawArchiveError(f"Unsafe archive path: {value!r}")
    return path


def iter_source_files(source_dir: Path) -> Iterator[tuple[Path, Path]]:
    """Yield source files in stable order while excluding authentication data."""

    source = source_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Raw source directory does not exist: {source}")
    files: list[tuple[Path, Path]] = []
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(source)
        if "_auth" in relative.parts:
            continue
        if candidate.is_symlink():
            raise RawArchiveError(f"Symlinks are forbidden in raw archives: {candidate}")
        if candidate.is_file():
            files.append((relative, candidate))
    yield from sorted(files, key=lambda item: item[0].as_posix())


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_environment(),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RawArchiveError(
            f"git {' '.join(args)} timed out after {timeout_seconds:g} seconds"
        ) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RawArchiveError(
            f"git {' '.join(args)} failed with exit code {result.returncode}: {detail}"
        )
    return result


def _is_transient_push_error(exc: RawArchiveError) -> bool:
    detail = str(exc).casefold()
    return any(marker in detail for marker in TRANSIENT_PUSH_ERROR_MARKERS)


def _push_branch(
    repository_dir: Path,
    *,
    timeout_seconds: float,
    attempts: int = PUSH_ATTEMPTS,
    retry_delay_seconds: float = PUSH_RETRY_DELAY_SECONDS,
) -> None:
    """Push the archive branch with bounded retries for transport failures.

    Commits remain in the isolated archive checkout when all attempts fail.
    The next publication run detects and pushes those pending commits before
    scanning source files, so no generated archive work is lost.
    """

    if attempts < 1:
        raise ValueError("Push attempts must be at least 1")
    last_error: RawArchiveError | None = None
    for attempt in range(1, attempts + 1):
        try:
            run_git(
                ["push", "origin", f"HEAD:refs/heads/{TARGET_BRANCH}"],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
            return
        except RawArchiveError as exc:
            last_error = exc
            if attempt == attempts or not _is_transient_push_error(exc):
                raise
            delay = retry_delay_seconds * (2 ** (attempt - 1))
            print(
                f"Raw archive push attempt {attempt}/{attempts} failed with a "
                f"transient transport error; retrying in {delay:g} seconds.\n{exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    # The loop either returns or raises. Keep the guard explicit for type
    # checkers and for safety if its control flow changes later.
    raise RawArchiveError(f"Raw archive push failed: {last_error}")


def _configure_git(repository_dir: Path, timeout_seconds: float) -> None:
    settings = (
        ("user.name", "GlobalID Raw Archive"),
        ("user.email", "raw-archive@globalid.invalid"),
        ("core.compression", "1"),
        ("pack.compression", "1"),
        ("pack.window", "0"),
        ("pack.depth", "0"),
    )
    for key, value in settings:
        run_git(["config", key, value], repository_dir, timeout_seconds=timeout_seconds)


def _working_tree_has_tracked_changes(repository_dir: Path, timeout_seconds: float) -> bool:
    unstaged = run_git(
        ["diff", "--quiet"], repository_dir, timeout_seconds=timeout_seconds, check=False
    )
    staged = run_git(
        ["diff", "--cached", "--quiet"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if unstaged.returncode not in (0, 1) or staged.returncode not in (0, 1):
        raise RawArchiveError("Unable to inspect the archive working tree")
    return unstaged.returncode == 1 or staged.returncode == 1


def _remote_branch_exists(repo_url: str, cwd: Path, timeout_seconds: float) -> bool:
    result = run_git(
        ["ls-remote", "--heads", "--", repo_url, f"refs/heads/{TARGET_BRANCH}"],
        cwd,
        timeout_seconds=timeout_seconds,
    )
    return bool(result.stdout.strip())


def _switch_existing_clone_to_main(
    repository_dir: Path,
    *,
    remote_has_main: bool,
    timeout_seconds: float,
) -> None:
    branch = run_git(
        ["symbolic-ref", "--short", "HEAD"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    if branch == TARGET_BRANCH:
        return
    if _working_tree_has_tracked_changes(repository_dir, timeout_seconds):
        raise RawArchiveError(
            f"Archive clone is on {branch!r} with tracked local changes; "
            f"cannot switch safely to {TARGET_BRANCH!r}"
        )

    local_main = run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{TARGET_BRANCH}"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if local_main.returncode == 0:
        run_git(["checkout", TARGET_BRANCH], repository_dir, timeout_seconds=timeout_seconds)
    elif remote_has_main:
        run_git(
            ["checkout", "-b", TARGET_BRANCH, f"origin/{TARGET_BRANCH}"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
    else:
        run_git(
            ["checkout", "--orphan", TARGET_BRANCH],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )


def _fast_forward_from_remote(repository_dir: Path, timeout_seconds: float) -> None:
    remote_ref = f"origin/{TARGET_BRANCH}"
    local_has_commit = run_git(
        ["rev-parse", "--verify", "HEAD"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    ).returncode == 0
    if not local_has_commit:
        run_git(["reset", "--mixed", remote_ref], repository_dir, timeout_seconds=timeout_seconds)
        run_git(["checkout", "--", "."], repository_dir, timeout_seconds=timeout_seconds)
        return

    remote_is_ancestor = run_git(
        ["merge-base", "--is-ancestor", remote_ref, "HEAD"],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if remote_is_ancestor.returncode == 0:
        return
    local_is_ancestor = run_git(
        ["merge-base", "--is-ancestor", "HEAD", remote_ref],
        repository_dir,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    if local_is_ancestor.returncode == 0:
        run_git(["merge", "--ff-only", remote_ref], repository_dir, timeout_seconds=timeout_seconds)
        return
    raise RawArchiveError(
        f"Local and remote {TARGET_BRANCH!r} histories diverged; refusing to overwrite either side"
    )


def _push_pending_commits(repository_dir: Path, timeout_seconds: float) -> None:
    """Resume a commit whose previous network push was interrupted or rejected."""

    pending = run_git(
        ["rev-list", "--count", f"origin/{TARGET_BRANCH}..HEAD"],
        repository_dir,
        timeout_seconds=timeout_seconds,
    )
    if int(pending.stdout.strip() or "0") > 0:
        _push_branch(
            repository_dir,
            timeout_seconds=timeout_seconds,
        )


def _ensure_repository(
    repository_dir: Path,
    *,
    repo_url: str,
    push: bool,
    timeout_seconds: float,
) -> None:
    repository_dir = repository_dir.resolve()
    if push and not repo_url:
        raise RawArchiveError("--push requires --repo-url or RAW_ARCHIVE__REPO_URL")

    if (repository_dir / ".git").is_dir():
        configured = run_git(
            ["remote", "get-url", "origin"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if repo_url:
            if configured.returncode != 0:
                run_git(
                    ["remote", "add", "origin", repo_url],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                )
            elif configured.stdout.strip() != repo_url:
                # The URL may legitimately change between SSH and HTTPS. The
                # explicit URL for this run is authoritative.
                run_git(
                    ["remote", "set-url", "origin", repo_url],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                )

        if push:
            remote_has_main = _remote_branch_exists(repo_url, repository_dir, timeout_seconds)
            if remote_has_main:
                run_git(
                    [
                        "fetch",
                        "origin",
                        f"+refs/heads/{TARGET_BRANCH}:refs/remotes/origin/{TARGET_BRANCH}",
                    ],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                )
            _switch_existing_clone_to_main(
                repository_dir,
                remote_has_main=remote_has_main,
                timeout_seconds=timeout_seconds,
            )
            if remote_has_main:
                _fast_forward_from_remote(repository_dir, timeout_seconds)
                _push_pending_commits(repository_dir, timeout_seconds)
        else:
            branch = run_git(
                ["symbolic-ref", "--short", "HEAD"],
                repository_dir,
                timeout_seconds=timeout_seconds,
            ).stdout.strip()
            if branch != TARGET_BRANCH:
                raise RawArchiveError(
                    f"Local-only archive clone is on {branch!r}; expected {TARGET_BRANCH!r}"
                )
        _configure_git(repository_dir, timeout_seconds)
        return

    if repository_dir.exists() and any(repository_dir.iterdir()):
        raise RawArchiveError(f"Archive repository directory is not empty: {repository_dir}")
    repository_dir.mkdir(parents=True, exist_ok=True)

    remote_has_main = push and _remote_branch_exists(repo_url, ROOT, timeout_seconds)
    if remote_has_main:
        repository_dir.rmdir()
        run_git(
            [
                "clone",
                "--branch",
                TARGET_BRANCH,
                "--single-branch",
                "--",
                repo_url,
                str(repository_dir),
            ],
            ROOT,
            timeout_seconds=timeout_seconds,
        )
    else:
        run_git(["init", "--quiet"], repository_dir, timeout_seconds=timeout_seconds)
        run_git(
            ["checkout", "--orphan", TARGET_BRANCH],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
        if push:
            run_git(
                ["remote", "add", "origin", repo_url],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
    _configure_git(repository_dir, timeout_seconds)


def _atomic_copy(source: Path, destination: Path, *, size: int, sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.sync-", dir=destination.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle, length=4 * 1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.stat().st_size != size or _sha256_file(temporary) != sha256:
            raise RawArchiveError(f"Source changed while it was copied: {source}")
        shutil.copystat(source, temporary)
        temporary.replace(destination)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


class _PartWriter:
    def __init__(self, directory: Path, relative_prefix: PurePosixPath):
        self.directory = directory
        self.relative_prefix = relative_prefix
        self.handle: BinaryIO | None = None
        self.digest: hashlib._Hash | None = None
        self.size = 0
        self.index = 0
        self.parts: list[PartRecord] = []

    def _open(self) -> None:
        self.index += 1
        self.handle = (self.directory / f"part-{self.index:04d}").open("wb")
        self.digest = hashlib.sha256()
        self.size = 0

    def _close(self) -> None:
        if self.handle is None or self.digest is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        name = f"part-{self.index:04d}"
        self.parts.append(
            PartRecord(
                path=(self.relative_prefix / name).as_posix(),
                size=self.size,
                sha256=self.digest.hexdigest(),
            )
        )
        self.handle = None
        self.digest = None
        self.size = 0

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self.handle is None:
                self._open()
            available = PART_BYTES - self.size
            piece = view[:available]
            written = self.handle.write(piece)
            if written is None:
                written = len(piece)
            assert self.digest is not None
            self.digest.update(piece[:written])
            self.size += written
            view = view[written:]
            if self.size == PART_BYTES:
                self._close()

    def close(self) -> tuple[PartRecord, ...]:
        self._close()
        return tuple(self.parts)


def _parts_are_current(repository_dir: Path, record: LargeFileRecord) -> bool:
    if not record.parts:
        return False
    for part in record.parts:
        relative = _safe_relative_path(part.path)
        path = repository_dir.joinpath(*relative.parts)
        if (
            not path.is_file()
            or path.stat().st_size != part.size
            or _sha256_file(path) != part.sha256
        ):
            return False
    return sum(part.size for part in record.parts) == record.size


def _split_file(
    source: Path,
    repository_dir: Path,
    relative: PurePosixPath,
    *,
    size: int,
    sha256: str,
) -> LargeFileRecord:
    split_relative = DATA_ROOT / PurePosixPath(f"{relative.as_posix()}.parts")
    destination = repository_dir.joinpath(*split_relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.sync-", dir=destination.parent))
    writer = _PartWriter(temporary, split_relative)
    combined_digest = hashlib.sha256()
    combined_size = 0
    try:
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                combined_digest.update(block)
                combined_size += len(block)
                writer.write(block)
        parts = writer.close()
        if combined_size != size or combined_digest.hexdigest() != sha256:
            raise RawArchiveError(f"Source changed while it was split: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        temporary.rename(destination)
        return LargeFileRecord(path=relative.as_posix(), size=size, sha256=sha256, parts=parts)
    except Exception:
        writer.close()
        shutil.rmtree(temporary, ignore_errors=True)
        raise


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
        "`data/raw` with original paths and bytes. Authentication data under `_auth` is excluded.\n\n"
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
) -> ArchiveResult:
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

        for index, (relative_path, source_path) in enumerate(source_entries, start=1):
            relative = PurePosixPath(relative_path.as_posix())
            size, digest = _source_identity(source_path)
            source_bytes += size
            direct_relative = DATA_ROOT / relative
            direct_destination = repository_root.joinpath(*direct_relative.parts)
            split_relative = PurePosixPath(f"{direct_relative.as_posix()}.parts")
            split_destination = repository_root.joinpath(*split_relative.parts)

            if size <= MAX_DIRECT_FILE_BYTES:
                existed = direct_destination.exists() or split_destination.exists()
                current = (
                    direct_destination.is_file()
                    and direct_destination.stat().st_size == size
                    and _sha256_file(direct_destination) == digest
                )
                if not current:
                    report_change("copy", index, relative, size)
                    _atomic_copy(source_path, direct_destination, size=size, sha256=digest)
                    if existed:
                        updated += 1
                    else:
                        added += 1
                if split_destination.exists():
                    shutil.rmtree(split_destination)
                expected_storage_files.add(direct_relative.as_posix())
                sync_items.append(
                    _SyncItem(
                        source_path=relative.as_posix(),
                        storage_paths=(direct_relative.as_posix(), split_relative.as_posix()),
                        stored_bytes=size,
                    )
                )
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
            if direct_destination.exists():
                direct_destination.unlink()
            large_files.append(record)
            part_paths = tuple(part.path for part in record.parts)
            expected_storage_files.update(part_paths)
            for part_index, part in enumerate(record.parts, start=1):
                storage_paths = [part.path]
                if part_index == 1:
                    storage_paths.append(direct_relative.as_posix())
                sync_items.append(
                    _SyncItem(
                        source_path=f"{relative.as_posix()} (part {part_index})",
                        storage_paths=tuple(storage_paths),
                        stored_bytes=part.size,
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
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
