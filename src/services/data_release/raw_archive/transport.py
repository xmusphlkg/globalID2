"""Raw archive Git invocation and bounded transient transport retries."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .contracts import RawArchiveError


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git(
    args: list[str],
    cwd: Path,
    *,
    environment=_git_environment,
    timeout_seconds: float = 30 * 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=environment(),
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


def _push_branch(
    repository_dir: Path,
    *,
    timeout_seconds: float,
    run_git,
    branch: str,
    is_transient_error,
    sleep,
    attempts: int = 4,
    retry_delay_seconds: float = 2.0,
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
                ["push", "origin", f"HEAD:refs/heads/{branch}"],
                repository_dir,
                timeout_seconds=timeout_seconds,
            )
            return
        except RawArchiveError as exc:
            last_error = exc
            if attempt == attempts or not is_transient_error(exc):
                raise
            delay = retry_delay_seconds * (2 ** (attempt - 1))
            print(
                f"Raw archive push attempt {attempt}/{attempts} failed with a "
                f"transient transport error; retrying in {delay:g} seconds.\n{exc}",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay)
    # The loop either returns or raises. Keep the guard explicit for type
    # checkers and for safety if its control flow changes later.
    raise RawArchiveError(f"Raw archive push failed: {last_error}")
