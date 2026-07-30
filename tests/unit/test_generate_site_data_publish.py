from __future__ import annotations


def test_ensure_download_repo_resets_existing_cache_to_remote_branch(
    tmp_path, monkeypatch
):
    from scripts import generate_site_data

    workdir = tmp_path / "download-repo"
    (workdir / ".git").mkdir(parents=True)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        generate_site_data, "remote_branch_exists", lambda *_args, **_kwargs: True
    )

    def fake_run_git(args, *_positional, **_kwargs):
        calls.append(list(args))
        if args == ["remote", "get-url", "origin"]:
            return "git@github.com:xmusphlkg/globalID2_data_download.git"
        return ""

    monkeypatch.setattr(generate_site_data, "run_git", fake_run_git)

    generate_site_data.ensure_download_repo(
        "git@github.com:xmusphlkg/globalID2_data_download.git",
        "master",
        workdir,
    )

    assert ["fetch", "--prune", "origin", "master"] in calls
    assert ["checkout", "-B", "master", "origin/master"] in calls
    assert ["reset", "--hard", "origin/master"] in calls
    assert not any(call[:1] == ["pull"] for call in calls)


def test_ensure_download_repo_reclones_when_existing_cache_is_corrupt(
    tmp_path, monkeypatch
):
    from scripts import generate_site_data

    workdir = tmp_path / "download-repo"
    (workdir / ".git").mkdir(parents=True)
    calls: list[list[str]] = []
    removed: list[str] = []

    monkeypatch.setattr(
        generate_site_data, "remote_branch_exists", lambda *_args, **_kwargs: True
    )

    def fake_run_git(args, *_positional, **_kwargs):
        calls.append(list(args))
        if args == ["remote", "get-url", "origin"]:
            return "git@github.com:xmusphlkg/globalID2_data_download.git"
        if args == ["fetch", "--prune", "origin", "master"]:
            raise RuntimeError("corrupt object")
        return ""

    def fake_rmtree(path):
        removed.append(str(path))
        (workdir / ".git").rmdir()
        workdir.rmdir()

    monkeypatch.setattr(generate_site_data, "run_git", fake_run_git)
    monkeypatch.setattr(generate_site_data.shutil, "rmtree", fake_rmtree)

    generate_site_data.ensure_download_repo(
        "git@github.com:xmusphlkg/globalID2_data_download.git",
        "master",
        workdir,
    )

    assert removed == [str(workdir)]
    assert [
        "clone",
        "--branch",
        "master",
        "git@github.com:xmusphlkg/globalID2_data_download.git",
        str(workdir),
    ] in calls
