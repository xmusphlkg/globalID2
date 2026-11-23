import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.domain.task import TaskStatus, TaskType
from src.services.data_release.download_manifest_retention import retire_download_manifest


@pytest.fixture
def release(tmp_path):
    task_id = str(uuid4())
    root = tmp_path / "exports"
    run = root / "surveillance-runs" / task_id
    (run / "downloads").mkdir(parents=True)
    manifest = run / "downloads/manifest.json"
    body = b'{"published":"catalogue"}'
    manifest.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    target = {"repo_url": "https://example.org/downloads.git", "branch": "main"}
    (run / "ready.json").write_text(json.dumps({"task_uuid": task_id, "downloads_manifest_sha256": digest}))
    (run / "downloads-published.json").write_text(json.dumps({"task_uuid": task_id, "manifest_sha256": digest, **target}))
    task = SimpleNamespace(task_uuid=task_id, status=TaskStatus.COMPLETED, task_type=TaskType.EXPORT_DATA,
        input_data={"download_repo_branch": target["branch"], "github_repo_url": target["repo_url"]},
        output_data={"release_id": f"data-{task_id}", "data_line": "surveillance",
                     "direct_downloads_published": True, "downloads_manifest_sha256": digest})
    repo = root / "site-downloads"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                                        *args], cwd=repo)
    git("init", "-qb", "main")
    (repo / "manifest.json").write_bytes(body)
    git("add", "--", "manifest.json")
    git("commit", "-qm", f"chore(data): publish surveillance data-{task_id}")
    return task, manifest, git


def test_retire_only_verified_private_copy(tmp_path, release):
    task, manifest, git = release
    allocated = manifest.stat().st_blocks * 512
    assert retire_download_manifest(tmp_path, task, apply=False) == allocated
    body = manifest.read_bytes()
    assert retire_download_manifest(tmp_path, task, apply=True) == allocated
    assert not manifest.exists()
    assert git("show", "main:manifest.json") == body
    assert (manifest.parents[1] / "ready.json").is_file()
    assert (manifest.parents[1] / "downloads-published.json").is_file()
    assert retire_download_manifest(tmp_path, task, apply=True) == 0


def test_unreadable_git_object_keeps_private_copy(tmp_path, release):
    task, manifest, git = release
    oid = git("rev-parse", "main:manifest.json").decode().strip()
    obj = tmp_path / "exports/site-downloads/.git/objects" / oid[:2] / oid[2:]
    obj.chmod(0o600)
    obj.write_bytes(obj.read_bytes()[:-10])
    assert retire_download_manifest(tmp_path, task, apply=True) == 0
    assert manifest.exists()


def test_parallel_cleanup_retires_once(tmp_path, release):
    task, manifest, _ = release
    allocated = manifest.stat().st_blocks * 512
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(retire_download_manifest, tmp_path, task, apply=True) for _ in range(4)]
    assert sorted(future.result() for future in results) == [0, 0, 0, allocated]


@pytest.mark.parametrize("reason", ["failed", "receipt", "changed_file", "changed_git", "symlink"])
def test_retains_unproven_or_retryable_candidate(tmp_path, release, reason):
    task, manifest, git = release
    if reason == "failed":
        task.status = TaskStatus.FAILED
    elif reason == "receipt":
        task.input_data["download_repo_branch"] = "other"
    elif reason == "changed_file":
        manifest.write_bytes(b'{"published":"cataloguE"}')
    elif reason == "changed_git":
        repo_file = tmp_path / "exports/site-downloads/manifest.json"
        repo_file.write_bytes(b'{"published":"cataloguE"}')
        git("commit", "-qam", f"chore(data): publish surveillance data-{task.task_uuid}")
    else:
        target = tmp_path / "saved-copy"
        manifest.rename(target)
        manifest.symlink_to(target)
    assert retire_download_manifest(tmp_path, task, apply=True) == 0
    assert manifest.exists()
