from datetime import datetime, timezone

from scripts.release_readiness import report


def test_readiness_blocks_dirty_worktree_and_preserves_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.release_readiness.git_status_paths", lambda root: ["dashboard/openapi.json", "new.py"])
    result = report(root=tmp_path, contract={"ready": True}, now=datetime(2026, 11, 13, tzinfo=timezone.utc))
    assert result["ready"] is False
    assert result["blockers"] == ["worktree_dirty"]
    assert result["repository"]["commit_dirty"] is True
    assert result["repository"]["dirty_paths"] == ["dashboard/openapi.json", "new.py"]


def test_readiness_blocks_stale_dashboard_contract_on_clean_tree(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.release_readiness.git_status_paths", lambda root: [])
    result = report(root=tmp_path, contract={"ready": False, "openapi_json": {"status": "stale"}})
    assert result["ready"] is False
    assert result["blockers"] == ["dashboard_contract_unverified"]
    assert result["repository"]["commit_dirty"] is False


def test_readiness_is_ready_only_when_tree_and_contract_are_verified(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.release_readiness.git_status_paths", lambda root: [])
    result = report(root=tmp_path, contract={"ready": True})
    assert result["ready"] is True
    assert result["blockers"] == []
