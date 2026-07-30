from __future__ import annotations


def test_ensure_download_repo_rebuilds_existing_cache_with_filtered_shallow_clone(
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
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--filter=blob:none",
        "--no-checkout",
        "--branch",
        "master",
        "git@github.com:xmusphlkg/globalID2_data_download.git",
        str(workdir),
    ] in calls
    assert ["reset", "--mixed", "origin/master"] in calls


def test_clone_download_repo_cleans_partial_checkout_before_retry(
    tmp_path, monkeypatch
):
    from scripts import generate_site_data

    workdir = tmp_path / "download-repo"
    calls: list[list[str]] = []
    removed: list[str] = []
    attempts = 0

    def fake_run_git(args, *_positional, **_kwargs):
        nonlocal attempts
        calls.append(list(args))
        if args[0] == "clone":
            attempts += 1
            workdir.mkdir(parents=True)
            if attempts == 1:
                raise RuntimeError("interrupted clone")
        return ""

    def fake_rmtree(path):
        removed.append(str(path))
        workdir.rmdir()

    monkeypatch.setattr(generate_site_data, "run_git", fake_run_git)
    monkeypatch.setattr(generate_site_data.shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(generate_site_data.time, "sleep", lambda *_args: None)

    generate_site_data.clone_download_repo(
        "git@github.com:xmusphlkg/globalID2_data_download.git",
        "master",
        workdir,
        branch_exists=True,
    )

    assert attempts == 2
    assert removed == [str(workdir)]
    assert ["reset", "--mixed", "origin/master"] in calls
