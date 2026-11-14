"""Repository adoption and fast-forward recovery with explicit transport ownership."""
from __future__ import annotations

from pathlib import Path

from .contracts import RawArchiveError


class GitRepository:
    """Keep branch, root and replaceable transport bound to one publication call."""

    def __init__(self, *, run_git, push_branch, branch: str, root: Path):
        self.run_git = run_git
        self.push_branch = push_branch
        self.branch = branch
        self.root = root

    def configure_git(self, repository_dir: Path, timeout_seconds: float) -> None:
        settings = (
            ("user.name", "GlobalID Raw Archive"),
            ("user.email", "raw-archive@globalid.invalid"),
            ("core.compression", "1"),
            ("pack.compression", "1"),
            ("pack.window", "0"),
            ("pack.depth", "0"),
        )
        for key, value in settings:
            self.run_git(["config", key, value], repository_dir, timeout_seconds=timeout_seconds)

    def working_tree_has_tracked_changes(self, repository_dir: Path, timeout_seconds: float) -> bool:
        unstaged = self.run_git(
            ["diff", "--quiet"], repository_dir, timeout_seconds=timeout_seconds, check=False
        )
        staged = self.run_git(
            ["diff", "--cached", "--quiet"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if unstaged.returncode not in (0, 1) or staged.returncode not in (0, 1):
            raise RawArchiveError("Unable to inspect the archive working tree")
        return unstaged.returncode == 1 or staged.returncode == 1

    def remote_branch_exists(self, repo_url: str, cwd: Path, timeout_seconds: float) -> bool:
        result = self.run_git(
            ["ls-remote", "--heads", "--", repo_url, f"refs/heads/{self.branch}"],
            cwd,
            timeout_seconds=timeout_seconds,
        )
        return bool(result.stdout.strip())

    def switch_existing_clone_to_main(self,
        repository_dir: Path,
        *,
        remote_has_main: bool,
        timeout_seconds: float,
    ) -> None:
        branch = self.run_git(
            ["symbolic-ref", "--short", "HEAD"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        ).stdout.strip()
        if branch == self.branch:
            return
        if self.working_tree_has_tracked_changes(repository_dir, timeout_seconds):
            raise RawArchiveError(
                f"Archive clone is on {branch!r} with tracked local changes; "
                f"cannot switch safely to {self.branch!r}"
            )

        local_main = self.run_git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{self.branch}"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if local_main.returncode == 0:
            self.run_git(["checkout", self.branch], repository_dir, timeout_seconds=timeout_seconds)
        elif remote_has_main:
            self.run_git(
                ["checkout", "-b", self.branch, f"origin/{self.branch}"],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
        else:
            self.run_git(
                ["checkout", "--orphan", self.branch],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )

    def fast_forward_from_remote(self, repository_dir: Path, timeout_seconds: float) -> None:
        remote_ref = f"origin/{self.branch}"
        local_has_commit = self.run_git(
            ["rev-parse", "--verify", "HEAD"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        ).returncode == 0
        if not local_has_commit:
            self.run_git(["reset", "--mixed", remote_ref], repository_dir, timeout_seconds=timeout_seconds)
            self.run_git(["checkout", "--", "."], repository_dir, timeout_seconds=timeout_seconds)
            return

        remote_is_ancestor = self.run_git(
            ["merge-base", "--is-ancestor", remote_ref, "HEAD"],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if remote_is_ancestor.returncode == 0:
            return
        local_is_ancestor = self.run_git(
            ["merge-base", "--is-ancestor", "HEAD", remote_ref],
            repository_dir,
            timeout_seconds=timeout_seconds,
            check=False,
        )
        if local_is_ancestor.returncode == 0:
            self.run_git(["merge", "--ff-only", remote_ref], repository_dir, timeout_seconds=timeout_seconds)
            return
        raise RawArchiveError(
            f"Local and remote {self.branch!r} histories diverged; refusing to overwrite either side"
        )

    def push_pending_commits(self, repository_dir: Path, timeout_seconds: float) -> None:
        """Resume a commit whose previous network push was interrupted or rejected."""

        pending = self.run_git(
            ["rev-list", "--count", f"origin/{self.branch}..HEAD"],
            repository_dir,
            timeout_seconds=timeout_seconds,
        )
        if int(pending.stdout.strip() or "0") > 0:
            self.push_branch(
                repository_dir,
                timeout_seconds=timeout_seconds,
            )

    def ensure_repository(self,
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
            configured = self.run_git(
                ["remote", "get-url", "origin"],
                repository_dir,
                timeout_seconds=timeout_seconds,
                check=False,
            )
            if repo_url:
                if configured.returncode != 0:
                    self.run_git(
                        ["remote", "add", "origin", repo_url],
                        repository_dir,
                        timeout_seconds=timeout_seconds,
                    )
                elif configured.stdout.strip() != repo_url:
                    # The URL may legitimately change between SSH and HTTPS. The
                    # explicit URL for this run is authoritative.
                    self.run_git(
                        ["remote", "set-url", "origin", repo_url],
                        repository_dir,
                        timeout_seconds=timeout_seconds,
                    )

            if push:
                remote_has_main = self.remote_branch_exists(repo_url, repository_dir, timeout_seconds)
                if remote_has_main:
                    self.run_git(
                        [
                            "fetch",
                            "origin",
                            f"+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}",
                        ],
                        repository_dir,
                        timeout_seconds=timeout_seconds,
                    )
                self.switch_existing_clone_to_main(
                    repository_dir,
                    remote_has_main=remote_has_main,
                    timeout_seconds=timeout_seconds,
                )
                if remote_has_main:
                    self.fast_forward_from_remote(repository_dir, timeout_seconds)
                    self.push_pending_commits(repository_dir, timeout_seconds)
            else:
                branch = self.run_git(
                    ["symbolic-ref", "--short", "HEAD"],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                ).stdout.strip()
                if branch != self.branch:
                    raise RawArchiveError(
                        f"Local-only archive clone is on {branch!r}; expected {self.branch!r}"
                    )
            self.configure_git(repository_dir, timeout_seconds)
            return

        if repository_dir.exists() and any(repository_dir.iterdir()):
            raise RawArchiveError(f"Archive repository directory is not empty: {repository_dir}")
        repository_dir.mkdir(parents=True, exist_ok=True)

        remote_has_main = push and self.remote_branch_exists(repo_url, self.root, timeout_seconds)
        if remote_has_main:
            repository_dir.rmdir()
            self.run_git(
                [
                    "clone",
                    "--branch",
                    self.branch,
                    "--single-branch",
                    "--",
                    repo_url,
                    str(repository_dir),
                ],
                self.root,
                timeout_seconds=timeout_seconds,
            )
        else:
            self.run_git(["init", "--quiet"], repository_dir, timeout_seconds=timeout_seconds)
            self.run_git(
                ["checkout", "--orphan", self.branch],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
            if push:
                self.run_git(
                    ["remote", "add", "origin", repo_url],
                    repository_dir,
                    timeout_seconds=timeout_seconds,
                )
        self.configure_git(repository_dir, timeout_seconds)
