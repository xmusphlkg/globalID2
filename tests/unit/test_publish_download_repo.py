from __future__ import annotations

import hashlib
import json

from scripts.publish_download_repo import sync_managed_assets, validate_source


def _write_source(root, payload: bytes = b"current partition"):
    base = "https://raw.githubusercontent.com/example/data/main"
    files = {}
    for format_name in ("csv", "json", "xlsx"):
        relative = f"diseases/d007/2026-2029.{format_name}"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = payload + format_name.encode()
        path.write_bytes(content)
        files[format_name] = {
            "url": f"{base}/{relative}",
            "relative_path": relative,
            "filename": f"globalid-d007-2026-2029.{format_name}",
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    (root / "countries").mkdir(exist_ok=True)
    manifest = {
        "schema_version": 3,
        "formats": ["csv", "json", "xlsx"],
        "download_url_base": base,
        "countries": [],
        "diseases": [
            {
                "id": "d007",
                "disease_id": "D007",
                "parts": [{"id": "2026-2029", "is_current": True, "files": files}],
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_validate_source_accepts_partitioned_three_format_manifest(tmp_path):
    _write_source(tmp_path)
    manifest = validate_source(tmp_path, "main")
    assert manifest["diseases"][0]["parts"][0]["id"] == "2026-2029"


def test_incremental_sync_copies_only_changed_partition(tmp_path):
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    _write_source(source)

    first = sync_managed_assets(source, checkout)
    second = sync_managed_assets(source, checkout)
    assert first["copied"] == 4
    assert second == {"copied": 0, "removed": 0}

    _write_source(source, payload=b"revised current partition")
    third = sync_managed_assets(source, checkout)
    assert third["copied"] == 4  # three current files plus manifest
