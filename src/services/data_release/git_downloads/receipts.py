"""Durable download acknowledgements bound to a repository, branch and commit."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .transport import run_git


def _receipt_path(workdir: Path, *, run_git=run_git) -> Path:
    return Path(run_git(["rev-parse", "--absolute-git-dir"], workdir)) / "download-publication.json"


def publication_is_verified(workdir: Path, repo_url: str, branch: str, *, run_git=run_git, receipt_path=_receipt_path) -> bool:
    try:
        receipt = json.loads(receipt_path(workdir).read_text())
        return (
            isinstance(receipt, dict)
            and receipt.get("schema") == 1
            and receipt.get("repo_url") == repo_url
            and receipt.get("branch") == branch
            and receipt.get("commit") == run_git(["rev-parse", "HEAD"], workdir)
            and not run_git(["status", "--porcelain", "--untracked-files=all"], workdir)
        )
    except (OSError, ValueError, RuntimeError):
        return False


def record_publication(workdir: Path, repo_url: str, branch: str, commit: str, *, receipt_path=_receipt_path) -> None:
    """Advance the durable receipt only after push and remote verification."""
    path = receipt_path(workdir)
    temporary = path.with_suffix(".tmp")
    receipt = {
        "schema": 1, "repo_url": repo_url, "branch": branch,
        "commit": commit, "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    # Keep the audit history outside public content so timestamps never dirty
    # a dataset. A crash before the latest pointer is replaced may repeat an
    # event on retry; the commit remains its stable publication identity.
    with (path.parent / "download-publications.jsonl").open("a") as journal:
        journal.write(json.dumps(receipt, sort_keys=True) + "\n")
        journal.flush()
        os.fsync(journal.fileno())
    with temporary.open("w") as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
