import hashlib
import json

import pytest

from scripts.install_co_bes_automation import freeze_runtime


def test_snapshot_pins_code_but_shares_evidence_and_secret_location(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/entry.py").write_text("VERSION = 1\n")
    (tmp_path / "src/.env.private.conf").write_text("never-copy")
    (tmp_path / ".env").write_text("SECRET=never-copy\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "venv").mkdir()
    first = freeze_runtime(tmp_path)
    assert (first / ".env").is_symlink()
    assert (first / "data").resolve() == tmp_path / "data"
    assert not (first / "src/.env.private.conf").exists()
    manifest = json.loads((first / "runtime-manifest.json").read_text())
    assert set(manifest["files"]) == {"src/entry.py"}
    assert "never-copy" not in (first / "runtime-manifest.json").read_text()
    (tmp_path / "src/entry.py").write_text("VERSION = 2\n")
    second = freeze_runtime(tmp_path)
    assert first != second
    assert (first / "src/entry.py").read_text() == "VERSION = 1\n"
    assert (tmp_path / "data/runtime/co-bes/current").resolve() == second


def test_existing_snapshot_tampering_is_rejected(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/entry.py").write_text("VERSION = 1\n")
    pinned = freeze_runtime(tmp_path)
    (pinned / "src/entry.py").write_text("VERSION = 2\n")
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        freeze_runtime(tmp_path)


def test_archive_replay_accepts_shared_raw_directory_without_escaping_country(tmp_path, monkeypatch):
    from scripts.imports import import_surveillance_archive as archive

    shared = tmp_path / "shared"
    country = shared / "raw/co"
    country.mkdir(parents=True)
    payload = b"original PDF bytes"
    (country / "report.pdf").write_bytes(payload)
    manifest = country / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": [{
        "relative_path": "data/raw/co/report.pdf", "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "source_url": "https://www.ins.gov.co/report.pdf",
        "kind": "cda_pdf", "metadata_": {},
    }]}))
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    (pinned / "data").symlink_to(shared, target_is_directory=True)
    monkeypatch.setattr(archive, "ROOT", pinned)
    artifacts = archive.load_archive_manifest(pinned / "data/raw/co/manifest.json", "CO")
    assert artifacts[0].relative_path == "data/raw/co/report.pdf"
    with pytest.raises(ValueError, match="must be under"):
        archive.load_archive_manifest(manifest, "NG")
