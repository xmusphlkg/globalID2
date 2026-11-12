"""Publish a frozen surveillance download candidate before acknowledging changes."""
from __future__ import annotations

import json

from src.generation.site_data_writer import write_compact_json

from .checks import build_git_env
from .commands import build_publish_download_repo_command


async def publish_download_candidate(service, task, *, runtime, run, manifest_sha256):
    data = task.input_data
    receipt_path = run / "downloads-published.json"
    identity = {"task_uuid": task.task_uuid, "manifest_sha256": manifest_sha256,
        "repo_url": data["github_repo_url"], "branch": data["download_repo_branch"]}
    if receipt_path.exists():
        if json.loads(receipt_path.read_text()) != identity:
            raise RuntimeError("Download publication receipt does not match the frozen candidate")
        return
    command = build_publish_download_repo_command(
        python_path=service._python_executable(), source_dir=run / "downloads",
        repo_url=data["github_repo_url"], branch=data["download_repo_branch"],
        commit_message=f"chore(data): publish surveillance data-{task.task_uuid}")
    command.extend(["--workdir", str(runtime.root_dir / "exports/site-downloads")])
    await service._run_logged_command(task.task_uuid, title="Publish Surveillance Downloads",
        cmd=command, cwd=runtime.root_dir,
        env=build_git_env(data["github_repo_url"],
            github_ssh_prefixes=("git@github.com:", "ssh://git@github.com/"), use_github_ssh_over_443=True),
        metadata={"event": "github_direct_download_publish", "release_id": f"data-{task.task_uuid}"},
        timeout_seconds=max(runtime.download_publish_timeout_seconds, 3 * 60 * 60))
    write_compact_json(receipt_path, identity)
