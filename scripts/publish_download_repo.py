#!/usr/bin/env python3
"""Sync generated download assets to a dedicated Git repository."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "astro-site" / "public" / "downloads"
DEFAULT_WORKDIR = Path("/tmp/globalid2-data-download-publish")
DEFAULT_REPO_URL = "git@github.com:xmusphlkg/globalID2_data_download.git"
MANAGED_PATHS = ("countries", "diseases", "manifest.json")


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout."""
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def remote_branch_exists(repo_url: str, branch: str) -> bool:
    """Return True when the remote branch already exists."""
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", repo_url, branch],
        check=True,
        text=True,
        capture_output=True,
    )
    return bool(completed.stdout.strip())


def ensure_repo(repo_url: str, branch: str, workdir: Path) -> None:
    """Clone or update the dedicated download repository."""
    if not (workdir / ".git").exists():
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.parent.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["git", "clone"]
        if remote_branch_exists(repo_url, branch):
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([repo_url, str(workdir)])
        subprocess.run(clone_cmd, check=True, text=True)
        run_git(["checkout", "-B", branch], workdir)
        return

    run_git(["fetch", "origin"], workdir)
    run_git(["checkout", "-B", branch], workdir)
    if remote_branch_exists(repo_url, branch):
        run_git(["pull", "--ff-only", "origin", branch], workdir)


def clean_managed_paths(workdir: Path) -> None:
    """Remove previously published managed files."""
    for relative in MANAGED_PATHS:
        target = workdir / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def copy_managed_assets(source_dir: Path, workdir: Path) -> None:
    """Copy generated download assets into the target repo."""
    for relative in MANAGED_PATHS:
        source = source_dir / relative
        target = workdir / relative
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.exists():
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError(f"Expected generated asset missing: {source}")


def write_readme(workdir: Path, manifest: dict) -> None:
    """Write a simple repo README for humans browsing the data repo."""
    generated_at = manifest.get("generated_at") or datetime.now(timezone.utc).isoformat()
    countries = len(manifest.get("countries") or [])
    diseases = len(manifest.get("diseases") or [])
    base = manifest.get("download_url_base") or ""
    readme = f"""# GlobalID Data Downloads

This repository stores the generated download artifacts for the GlobalID public site.

- Generated at: `{generated_at}`
- Country datasets: `{countries}`
- Disease datasets: `{diseases}`
- Manifest: [`manifest.json`](./manifest.json)

The publishing pipeline copies:

- `countries/*.json`
- `countries/*.csv`
- `diseases/*.json`
- `diseases/*.csv`
- `manifest.json`

Primary public base configured during generation:

`{base}`
"""
    (workdir / "README.md").write_text(readme, encoding="utf-8")


def commit_and_push(workdir: Path, branch: str, commit_message: str) -> bool:
    """Commit and push if there are changes."""
    status = run_git(["status", "--short"], workdir)
    if not status:
        print("No changes to publish.")
        return False

    run_git(["add", "countries", "diseases", "manifest.json", "README.md"], workdir)
    run_git(["commit", "-m", commit_message], workdir)
    run_git(["push", "origin", branch], workdir)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish generated download assets to a git repo")
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="Target git repository URL")
    parser.add_argument("--branch", default="main", help="Target branch")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR, help="Temporary local checkout path")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Generated downloads directory to publish",
    )
    parser.add_argument(
        "--commit-message",
        default="chore: update generated data downloads",
        help="Git commit message",
    )
    args = parser.parse_args()

    manifest_path = args.source_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing generated manifest: {manifest_path}. Run scripts/generate_site_data.py first."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_repo(args.repo_url, args.branch, args.workdir)
    clean_managed_paths(args.workdir)
    copy_managed_assets(args.source_dir, args.workdir)
    write_readme(args.workdir, manifest)
    pushed = commit_and_push(args.workdir, args.branch, args.commit_message)
    if pushed:
        print(f"Published download assets to {args.repo_url} ({args.branch})")


if __name__ == "__main__":
    main()
