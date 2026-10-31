"""Release provenance resolves actual Git worktrees without assuming .git/HEAD."""

import subprocess

import pytest

from src.services import data_release_service as module
from src.services.data_release_service import DataReleaseService


def git(directory, *args):
    return subprocess.run(
        ["git", *args], cwd=directory, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(params=["primary", "linked", "detached"])
def checkout(tmp_path, monkeypatch, request):
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "--initial-branch", "main")
    git(
        repository,
        "-c",
        "user.name=Release Test",
        "-c",
        "user.email=release@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    target, expected = repository, "main"
    if request.param != "primary":
        target = tmp_path / "release-worktree"
        if request.param == "linked":
            expected = "feature/site-release"
            git(repository, "worktree", "add", "-b", expected, str(target), "HEAD")
        else:
            expected = "detached"
            git(repository, "worktree", "add", "--detach", str(target), "HEAD")
        assert (target / ".git").is_file()
    monkeypatch.setattr(module, "ROOT_DIR", target)
    monkeypatch.delenv("DATA_RELEASE_GIT_BRANCH", raising=False)
    return target, expected


def test_sync_fallback_reports_full_branch_in_actual_worktree(checkout):
    _, expected = checkout
    assert DataReleaseService()._current_git_branch_fallback() == expected


async def test_async_branch_reports_actual_worktree(checkout):
    _, expected = checkout
    assert await DataReleaseService()._current_git_branch() == expected


def test_explicit_branch_fallback_is_preserved(checkout, monkeypatch):
    monkeypatch.setenv("DATA_RELEASE_GIT_BRANCH", "review/release")
    assert DataReleaseService()._current_git_branch_fallback() == "review/release"
