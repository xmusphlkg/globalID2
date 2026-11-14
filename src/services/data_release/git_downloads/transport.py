"""Git commands and resumable bounded uploads for partitioned downloads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run_git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        command = "git " + " ".join(args)
        raise RuntimeError(
            f"Git command failed with exit code {completed.returncode}: {command}"
            + (f"\n{output}" if output else "\nGit returned no diagnostic output.")
        )
    return completed.stdout.strip()


def run_git_with_retry(
    args: list[str],
    cwd: Path,
    *,
    run_git,
    is_transient_error,
    sleep,
    attempts: int = 4,
    retry_delay_seconds: float = 2.0,
) -> str:
    """Retry a Git network operation only for transient transport failures."""

    if attempts < 1:
        raise ValueError("Git attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return run_git(args, cwd)
        except RuntimeError as exc:
            if attempt == attempts or not is_transient_error(exc):
                raise
            delay = retry_delay_seconds * (2 ** (attempt - 1))
            print(
                f"Git network attempt {attempt}/{attempts} failed with a "
                f"transient error; retrying in {delay:g} seconds.\n{exc}",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay)
    raise AssertionError("unreachable Git retry state")


def push_download_snapshot(workdir: Path, branch: str, *, run_git, run_git_with_retry, push_branch, batch_bytes: int = 1024 * 1024 * 1024) -> None:
    """Preload bounded batches on a resumable ref, then atomically publish HEAD.

    GitHub limits each received pack to 2 GiB. A temporary branch makes each
    batch's objects available to the next push without exposing partial data
    on the public branch. Its name is content-addressed so a restarted export
    with the same tree can resume even if its commit timestamp changed.
    """
    if batch_bytes < 1:
        raise ValueError("Push batch size must be positive")
    target = run_git(["rev-parse", "HEAD"], workdir)
    tree = run_git(["rev-parse", "HEAD^{tree}"], workdir)
    branch_key = hashlib.sha256(branch.encode()).hexdigest()[:12]
    staging = f"globalid-upload/{branch_key}-{tree}"
    remote = run_git_with_retry(["ls-remote", "--heads", "origin", branch, staging], workdir)
    refs = {line.split()[1]: line.split()[0] for line in remote.splitlines()}
    remote_head = refs.get(f"refs/heads/{branch}")
    base = refs.get(f"refs/heads/{staging}") or remote_head
    staged = f"refs/heads/{staging}" in refs
    if base:
        run_git_with_retry(["fetch", "origin", base], workdir)
    if staged and remote_head and remote_head != base:
        run_git_with_retry(["fetch", "origin", remote_head], workdir)
        if subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote_head, base], cwd=workdir,
            capture_output=True,
        ).returncode != 0:
            raise RuntimeError("Public download branch advanced during staged upload")
    if remote_head and subprocess.run(
        ["git", "merge-base", "--is-ancestor", remote_head, target],
        cwd=workdir, capture_output=True,
    ).returncode != 0:
        # In particular, do not build a staging history on a concurrent remote
        # commit and then silently replace its files with our older snapshot.
        raise RuntimeError("Download publication is non-fast-forward: remote branch advanced; reconcile the local export repository")
    # A new repository needs an empty tree as the comparison/index base.
    empty_tree = subprocess.run(
        ["git", "hash-object", "-t", "tree", "--stdin", "-w"], cwd=workdir,
        input="", text=True, capture_output=True, check=True,
    ).stdout.strip()
    raw = run_git(["diff", "--raw", "-z", "--no-abbrev", "--no-renames", base or empty_tree, target], workdir)
    fields = raw.split("\0")
    changes: list[tuple[str, str, str]] = []
    for offset in range(0, len(fields) - 1, 2):
        meta, path = fields[offset].split(), fields[offset + 1]
        if meta[1] != "000000":  # Deletions need no uploaded blob.
            changes.append((meta[1], meta[3], path))
    sizes: dict[str, int] = {}
    if changes:
        checked = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objectsize)"], cwd=workdir,
            input="".join(f"{oid}\n" for _, oid, _ in changes),
            text=True, capture_output=True, check=True,
        )
        sizes = {oid: int(size) for oid, size in (line.split() for line in checked.stdout.splitlines())}
    if any(sizes[oid] > batch_bytes for _, oid, _ in changes):
        raise RuntimeError("A download blob exceeds the push batch limit")
    if sum(sizes[oid] for _, oid, _ in changes) > batch_bytes:
        with tempfile.TemporaryDirectory(prefix="globalid-upload-index-") as directory:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}

            def index_git(args: list[str], payload: str | None = None) -> str:
                return subprocess.run(
                    ["git", *args], cwd=workdir, env=env, input=payload,
                    text=True, capture_output=True, check=True,
                ).stdout.strip()

            index_git(["read-tree", base or empty_tree])
            batches: list[list[tuple[str, str, str]]] = []
            current: list[tuple[str, str, str]] = []
            current_bytes = 0
            for change in changes:
                size = sizes[change[1]]
                if current and current_bytes + size > batch_bytes:
                    batches.append(current)
                    current, current_bytes = [], 0
                current.append(change)
                current_bytes += size
            if current:
                batches.append(current)
            for number, batch in enumerate(batches, 1):
                index_git(["update-index", "-z", "--index-info"], "".join(
                    f"{mode} {oid}\t{path}\0" for mode, oid, path in batch
                ))
                staged_tree = index_git(["write-tree"])
                commit_args = ["commit-tree", staged_tree, "-m", "Stage bounded download objects"]
                if base:
                    commit_args.extend(["-p", base])
                staged_commit = run_git(commit_args, workdir)
                print(f"Upload download batch {number}/{len(batches)} ({sum(sizes[oid] for _, oid, _ in batch)} bytes)", flush=True)
                # Large generated partitions are already compressed as Git
                # objects. Avoid expensive delta searches for these bounded
                # backfill batches; ordinary small releases retain Git defaults.
                run_git_with_retry(
                    ["-c", "pack.window=0", "push", "origin", f"{staged_commit}:refs/heads/{staging}"],
                    workdir,
                )
                base = staged_commit
                staged = True
    if staged:
        # Git's push negotiation is scoped to the destination ref; merely
        # uploading blobs to an unrelated ref does not keep the final pack
        # small. Make the completed snapshot a child of the staging history,
        # so the final fast-forward contains only the remaining bounded blobs.
        message = run_git(["log", "-1", "--format=%B", target], workdir)
        final = run_git(["commit-tree", tree, "-p", base, "-m", message], workdir)
        run_git(["update-ref", f"refs/heads/{branch}", final, target], workdir)
    # This remains a normal fast-forward push: concurrent releases cannot be overwritten.
    push_branch(workdir, branch)
    if staged:
        try:
            run_git_with_retry(["push", "origin", "--delete", staging], workdir)
        except RuntimeError as exc:
            print(f"Published successfully; staging ref cleanup deferred: {exc}", file=sys.stderr, flush=True)
