from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import time

import pytest

from scripts import manage_r2_object_index as command
from src.services.data_release.r2_object_index import R2ObjectIndex, R2ObjectIndexError, R2ReservationConflict

ENDPOINT = "https://account.r2.example"


@pytest.fixture(autouse=True)
def restore_environment(monkeypatch):
    # The CLI intentionally loads its own environment. Track every key changed
    # by these tests so another test module never inherits their target.
    for key in ("R2_ENDPOINT_URL", "R2_PAPERS_BUCKET", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.setenv(key, os.environ.get(key, ""))


def arguments(tmp_path, mode, *extra):
    return [mode, "--env-file", str(tmp_path / "absent.env"), "--endpoint", ENDPOINT,
            "--bucket", "papers", "--index-path", str(tmp_path / "objects.sqlite3"), *extra]


def index(tmp_path):
    return R2ObjectIndex(ENDPOINT, "papers", path=tmp_path / "objects.sqlite3", max_total_bytes=100)


def inventory(tmp_path, **overrides):
    value = {"schema": 1, "endpoint": ENDPOINT, "bucket": "papers", "complete": True,
             "observed_at": datetime.now(timezone.utc).isoformat(),
             "objects": {"object": {"bytes": 40, "etag": "confirmed"}}}
    value.update(overrides)
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(value))
    return path, value


def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Offline administration must not perform remote requests or derive credentials")
    monkeypatch.setattr(command.R2S3Client, "_request", fail)
    monkeypatch.setattr(command, "_config_from_env", fail)


def test_status_is_offline_and_does_not_materialize_the_full_inventory(tmp_path, monkeypatch, capsys):
    db = index(tmp_path)
    db.replace_inventory({"object": {"bytes": 60, "etag": "exists"}})
    db.reserve("pending", 20)
    db.close()
    forbid_network(monkeypatch)
    monkeypatch.setattr(R2ObjectIndex, "snapshot", lambda self: pytest.fail("status must use aggregates"))
    assert command.main(arguments(tmp_path, "status", "--max-total-bytes", "100")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["object_count"] == 1 and result["stored_bytes"] == 60
    assert result["pending_count"] == 1 and result["reserved_bytes"] == 20
    assert result["inventory_fresh"] and not result["over_budget"]


def test_saved_import_is_offline_and_preserves_the_snapshot_age(tmp_path, monkeypatch, capsys):
    observed = datetime.fromtimestamp(time.time() - 90000, timezone.utc).isoformat()
    path, _ = inventory(tmp_path, observed_at=observed)
    forbid_network(monkeypatch)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    command.main(arguments(tmp_path, "reconcile", "--inventory", str(path), "--inventory-sha256", digest))
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "saved_inventory"
    assert result["stored_bytes"] == 40 and result["object_count"] == 1
    assert result["inventory_checked_at"] == datetime.fromisoformat(observed).timestamp()
    assert result["inventory_fresh"] is False


@pytest.mark.parametrize("change", [
    {"bucket": "other"}, {"endpoint": "https://other-account.r2.example"}, {"endpoint": None},
    {"complete": False}, {"truncated": True}, {"observed_at": "2026-01-01T00:00:00"},
    {"observed_at": "invalid"}, {"objects": []}, {"object_count": 99},
])
def test_invalid_saved_inventory_never_replaces_local_state(tmp_path, monkeypatch, change):
    db = index(tmp_path)
    db.replace_inventory({"original": {"bytes": 70, "etag": "old"}})
    path, _ = inventory(tmp_path, **change)
    forbid_network(monkeypatch)
    with pytest.raises(ValueError):
        command.main(arguments(tmp_path, "reconcile", "--inventory", str(path)))
    assert db.total_bytes() == 70 and db.get("original") is not None


def test_sha_mismatch_is_rejected(tmp_path):
    path, _ = inventory(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        command.load_inventory(path, endpoint=ENDPOINT, bucket="papers", expected_sha256="0" * 64)


def test_saved_inventory_cannot_erase_later_confirmed_uploads(tmp_path, monkeypatch):
    db = index(tmp_path)
    db.replace_inventory({})
    path, _ = inventory(tmp_path)
    db.record("new-upload", size=50, etag="new")
    forbid_network(monkeypatch)
    with pytest.raises(R2ObjectIndexError, match="predates"):
        command.main(arguments(tmp_path, "reconcile", "--inventory", str(path)))
    assert db.get("new-upload") is not None and db.total_bytes() == 50


def test_import_does_not_silently_clear_pending_reservations(tmp_path, monkeypatch):
    db = index(tmp_path)
    db.replace_inventory({})
    db.reserve("object", 40)
    path, _ = inventory(tmp_path)
    forbid_network(monkeypatch)
    with pytest.raises(R2ReservationConflict):
        command.main(arguments(tmp_path, "reconcile", "--inventory", str(path)))
    assert db.pending_bytes() == 40


def test_recovery_requires_an_explicit_successful_fence(tmp_path, monkeypatch, capsys):
    db = index(tmp_path)
    db.replace_inventory({})
    db.reserve("object", 40)
    path, _ = inventory(tmp_path)
    forbid_network(monkeypatch)
    with pytest.raises(SystemExit):
        command.main(arguments(tmp_path, "reconcile", "--inventory", str(path), "--recover-reservations"))
    checker = tmp_path / "fence.sh"
    checker.write_text("#!/bin/sh\nprintf 'sensitive checker output'\nexit 1\n")
    checker.chmod(0o700)
    args = arguments(tmp_path, "reconcile", "--inventory", str(path), "--recover-reservations", "--fence-check", str(checker))
    with pytest.raises(ValueError, match="fence check failed"):
        command.main(args)
    assert db.pending_bytes() == 40
    assert "sensitive checker output" not in capsys.readouterr().out
    checker.write_text("#!/bin/sh\nexit 0\n")
    command.main(args)
    assert db.pending_bytes() == 0 and db.total_bytes() == 40


def test_environment_loader_only_imports_cloudflare_and_r2_values(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "unchanged")
    path = tmp_path / ".env"
    path.write_text("R2_PAPERS_BUCKET=test-bucket\nCLOUDFLARE_ACCOUNT_ID=test-account\nUNRELATED_SECRET=changed\n")
    command._load_environment(path)
    assert os.environ["R2_PAPERS_BUCKET"] == "test-bucket"
    assert os.environ["CLOUDFLARE_ACCOUNT_ID"] == "test-account"
    assert os.environ["UNRELATED_SECRET"] == "unchanged"


def test_online_reconcile_requests_one_forced_refresh(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(command, "_config_from_env", lambda: object())

    class Client:
        def __init__(self, config, *, object_index, max_total_bytes):
            self.index = object_index
        def refresh_inventory(self, *, force):
            calls.append(force)
            self.index.replace_inventory({"remote": {"bytes": 20, "etag": "etag"}})
        def close(self):
            pass

    monkeypatch.setattr(command, "R2S3Client", Client)
    command.main(arguments(tmp_path, "reconcile"))
    assert calls == [True]
    assert json.loads(capsys.readouterr().out)["stored_bytes"] == 20


def test_online_recovery_checks_fence_before_and_after_complete_listing(tmp_path, monkeypatch, capsys):
    calls = []
    db = index(tmp_path)
    db.replace_inventory({})
    db.reserve("remote", 20)
    monkeypatch.setattr(command, "_config_from_env", lambda: object())
    monkeypatch.setattr(command, "_fence_checker", lambda path: lambda: calls.append("fence"))

    class Client:
        def __init__(self, config, **kwargs):
            pass
        @contextmanager
        def _index_lock(self, *, exclusive):
            assert exclusive
            calls.append("lock")
            yield
            calls.append("unlock")
        def list_objects(self):
            calls.append("list")
            return {"remote": {"bytes": 20, "etag": "etag"}}
        def close(self):
            pass

    monkeypatch.setattr(command, "R2S3Client", Client)
    command.main(arguments(tmp_path, "reconcile", "--recover-reservations", "--fence-check", "/unused/checker"))
    assert calls == ["fence", "lock", "fence", "list", "fence", "unlock"]
    assert db.pending_bytes() == 0 and db.total_bytes() == 20
