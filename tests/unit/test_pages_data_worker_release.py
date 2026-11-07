"""A template retry must retain the exact same-origin JSON delivery code."""
import pytest

from src.services.data_release.artifact_checkpoint import source_fingerprint, rendering_configuration
from src.services.data_release.build_reuse import BuildReuse


@pytest.mark.parametrize("name", ["src/pages.ts", "src/index.ts", "scripts/build-pages.mjs", "wrangler.jsonc", "package-lock.json"])
def test_worker_change_invalidates_static_candidate(tmp_path, name):
    path = tmp_path / "cloudflare/surveillance-data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original")
    before = source_fingerprint(tmp_path)
    reuse = BuildReuse(tmp_path, split=False, configuration={}, node_version="test")
    build_before = reuse.inputs()
    path.write_text("changed")
    assert source_fingerprint(tmp_path) != before
    assert reuse.inputs()["tree"] != build_before["tree"]


@pytest.mark.parametrize("flag", ["GIDS_PAGES_DATA_WORKER", "GIDS_RUNTIME_DATA_READY", "GIDS_RUNTIME_SITUATION_READY"])
def test_changing_data_delivery_mode_invalidates_rendering_receipt(tmp_path, monkeypatch, flag):
    monkeypatch.setenv(flag, "0")
    before = rendering_configuration(tmp_path)
    reuse = BuildReuse(tmp_path, split=False, configuration={}, node_version="test")
    build_before = reuse.inputs()
    monkeypatch.setenv(flag, "1")
    assert rendering_configuration(tmp_path) != before
    assert reuse.inputs()["environment"] != build_before["environment"]
