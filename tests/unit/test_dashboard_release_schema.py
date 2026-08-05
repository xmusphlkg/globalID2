from dashboard.api.schemas.release import DataReleaseChecksOut, DataReleaseConfigOut


def test_release_config_keeps_legacy_flags_for_rolling_upgrades():
    payload = DataReleaseConfigOut(
        enabled=True,
        timezone="UTC",
        poll_interval_seconds=30,
    ).model_dump()

    assert payload["commit_data_refresh_snapshot"] is False
    assert payload["push_data_refresh_snapshot"] is False


def test_release_checks_keep_safe_legacy_placeholders():
    payload = DataReleaseChecksOut(
        checked_at="2026-08-05T00:00:00Z",
        overall_ready=True,
        git={
            "env_var": "GITHUB_DATA_SHARE_REPO_URL",
            "branch": "master",
            "read_access_ok": True,
            "write_access_ok": True,
            "require_clean_worktree": True,
            "dirty_blocking_paths": [],
        },
        cloudflare={
            "token_present": True,
            "account_id_present": True,
            "project_access_ok": True,
        },
        commands={
            "python_path": "/usr/bin/python3",
            "python_exists": True,
            "wrangler_available": True,
        },
        repository_boundary={
            "generated_paths": ["data/raw"],
            "tracked_paths": [],
            "enforced": True,
        },
    ).model_dump()

    assert payload["git"]["dirty_release_paths"] == []
    assert payload["data_refresh_snapshot"] == {
        "enabled": False,
        "push_enabled": False,
        "script_path": "",
        "script_exists": False,
        "paths": [],
        "remote": "origin",
        "branch": "",
    }
