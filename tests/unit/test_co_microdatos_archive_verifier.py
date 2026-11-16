"""Backup verification must reconcile originals with the full source list."""
import hashlib
import json

from scripts import verify_co_microdatos_archive as verifier


def prepare(root):
    snapshot = root / "snapshots/test"
    snapshot.mkdir(parents=True)
    url = verifier.BASE + "/Microdatos/Datos_2025_210.xlsx"
    item = {"FileRef": "/Microdatos/Datos_2025_210.xlsx", "A_x00f1_o": "2025",
            "CodigoEvento": "210", "Modified": "2026-08-07", "File": {"Length": "8"}}
    payload = json.dumps({"d": {"results": [item]}}).encode()
    (snapshot / "catalogue.json").write_bytes(payload)
    evidence = {"artifacts": [{"path": "snapshots/test/catalogue.json", "byte_size": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest()}]}
    (snapshot / "manifest.json").write_text(json.dumps(evidence))
    (root / "original.xlsx").write_bytes(b"original")
    record = {"source_url": url, "path": "original.xlsx", "year": 2025, "event_code": "210",
              "event_name": "Dengue", "source_modified": item["Modified"], "captured_at": "2026-11-16",
              "byte_size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}
    manifest = {"catalogue_snapshot": "snapshots/test", "status": "complete", "errors": [], "files": [record]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_complete_originals_generate_portable_backup_inventory(tmp_path):
    prepare(tmp_path)
    result = verifier.verify(tmp_path)
    assert result["status"] == "complete_verified"
    assert result["verified_files"] == 1
    assert result["verified_bytes"] == 8
    assert "Datos_2025_210.xlsx" in (tmp_path / "inventory.csv").read_text(encoding="utf-8-sig")
    assert "original.xlsx" in (tmp_path / "SHA256SUMS").read_text()


def test_corrupted_original_cannot_be_certified(tmp_path):
    prepare(tmp_path)
    (tmp_path / "original.xlsx").write_bytes(b"tampered")
    result = verifier.verify(tmp_path)
    assert result["status"] == "incomplete"
    assert result["errors"][0]["error"] == "sha256_mismatch"
    assert not (tmp_path / "SHA256SUMS").exists()


def test_missing_source_entry_is_not_hidden_by_complete_status(tmp_path):
    manifest = prepare(tmp_path)
    manifest["files"] = []
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = verifier.verify(tmp_path)
    assert result["status"] == "incomplete"
    assert result["expected_files"] == 1
    assert result["verified_files"] == 0
    assert result["errors"][0]["error"] == "missing_file_record"
