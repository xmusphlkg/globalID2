"""Stage reuse validates payloads while allowing publisher bookkeeping."""
from pathlib import Path
import json

import pytest

from src.services.data_release.stage_receipts import StageReceipts


def _download_receipt(root: Path):
    downloads = root / "exports/site-downloads"
    countries = downloads / "countries"
    diseases = downloads / "diseases"
    countries.mkdir(parents=True)
    diseases.mkdir()
    (countries / "data.json").write_text('{"cases":1}')
    manifest = downloads / "manifest.json"
    manifest.write_text('{"countries":["data.json"]}')
    receipt = StageReceipts(root, "task-1", {"countries": ["US"]}, {})
    identity = {"release_id": "release-1"}
    signature = receipt.signature("ready", identity)
    assert receipt.save("ready", signature, [countries, diseases, manifest], identity=identity)
    return downloads, receipt, signature


def test_download_publisher_bookkeeping_does_not_invalidate_receipt(tmp_path):
    downloads, receipt, signature = _download_receipt(tmp_path)
    (downloads / "README.md").write_text("Publisher generated overview")
    (downloads / ".git").write_text("gitdir: ../.site-downloads.git")
    assert receipt.matches("ready", signature) is not None
    (downloads / "README.md").write_text("Updated overview")
    assert receipt.matches("ready", signature) is not None


@pytest.mark.parametrize("change", ["manifest", "artifact", "extra", "missing"])
def test_download_payload_changes_invalidate_receipt(tmp_path, change):
    downloads, receipt, signature = _download_receipt(tmp_path)
    if change == "manifest":
        (downloads / "manifest.json").write_text('{"countries":[]}')
    elif change == "artifact":
        (downloads / "countries/data.json").write_text('{"cases":2}')
    elif change == "extra":
        (downloads / "diseases/extra.json").write_text("{}")
    else:
        (downloads / "manifest.json").unlink()
    assert receipt.matches("ready", signature) is None


def test_symlink_directories_cannot_hide_untracked_payloads(tmp_path):
    downloads, receipt, signature = _download_receipt(tmp_path)
    (downloads / "countries/linked").symlink_to(downloads / "diseases", target_is_directory=True)
    assert receipt.matches("ready", signature) is None


def test_generated_data_survives_template_change_but_not_generator_change(tmp_path):
    generator = tmp_path / "src/generation/export.py"
    generator.parent.mkdir(parents=True)
    generator.write_text("contract = 1\n")
    template = tmp_path / "astro-site/src/pages/index.astro"
    template.parent.mkdir(parents=True)
    template.write_text("<h1>Old template</h1>")
    output = tmp_path / "exports/data.json"
    output.parent.mkdir()
    output.write_text('{"cases":1}')
    identity = {"release_id": "release-1"}
    receipt = StageReceipts(tmp_path, "task-1", {}, {})
    signature = receipt.signature("generated", identity)
    assert receipt.save("generated", signature, [output], identity=identity)
    template.write_text("<h1>Corrected template</h1>")
    assert receipt.signature("generated", identity) == signature
    assert receipt.matches("generated", signature) is not None
    generator.write_text("contract = 2\n")
    assert receipt.signature("generated", identity) != signature


def test_success_marker_preserves_own_retry_after_database_ack_failure(tmp_path):
    _, receipt, signature = _download_receipt(tmp_path)
    identity = {"release_id": "release-1"}
    receipt.mark_published(identity)
    reloaded = StageReceipts(tmp_path, "task-1", {"countries": ["US"]}, {})
    assert reloaded.signature("ready", identity) == signature
    assert reloaded.matches("ready", signature) is not None
    assert not (tmp_path / "exports/verified-pages-snapshot").exists()


def test_new_success_invalidates_old_receipt_even_if_pages_snapshot_was_not_retained(tmp_path):
    _, receipt, signature = _download_receipt(tmp_path)
    other = StageReceipts(tmp_path, "task-2", {}, {})
    other.mark_published({"release_id": "release-2"})
    updated_signature = receipt.signature("ready", {"release_id": "release-1"})
    assert updated_signature != signature
    assert receipt.matches("ready", updated_signature) is None


def test_same_task_release_id_with_different_built_bytes_cannot_reuse_old_candidate(tmp_path):
    _, receipt, signature = _download_receipt(tmp_path)
    receipt.mark_published({"release_id": "release-1", "build_digest": "different-built-content"})
    assert receipt.signature("ready", {"release_id": "release-1"}) != signature


def test_success_marker_takes_precedence_over_legacy_pages_snapshot(tmp_path):
    legacy = tmp_path / "exports/verified-pages-snapshot/release.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"release_id":"older"}')
    _, receipt, signature = _download_receipt(tmp_path)
    receipt.mark_published({"release_id": "release-1"})
    legacy.write_text('{"release_id":"stale-copy"}')
    assert receipt.signature("ready", {"release_id": "release-1"}) == signature
    reloaded = StageReceipts(tmp_path, "new-task", {}, {})
    baseline = reloaded.signature("ready", {"release_id": "release-3"})
    legacy.unlink()
    assert reloaded.signature("ready", {"release_id": "release-3"}) == baseline


def test_corrupted_success_marker_cannot_fall_back_to_stale_pages_snapshot(tmp_path):
    _, receipt, _ = _download_receipt(tmp_path)
    receipt.mark_published({"release_id": "release-1"})
    (tmp_path / "exports/successful-site-release.json").write_text("{")
    with pytest.raises(RuntimeError, match="Cannot validate successful release baseline"):
        receipt.signature("ready", {"release_id": "release-1"})


@pytest.mark.parametrize("change", ["none", "content", "extra", "missing"])
def test_code_only_requires_exact_successful_data_snapshot(tmp_path, change):
    downloads, receipt, _ = _download_receipt(tmp_path)
    paths = [downloads / "countries", downloads / "diseases"]
    receipt.mark_published({"release_id": "release-1"}, data_paths=paths)
    if change == "none":
        receipt.assert_published_data(paths)
        return
    if change == "content":
        (downloads / "countries/data.json").write_text('{"cases":2}')
    elif change == "extra":
        (downloads / "diseases/new.json").write_text("{}")
    else:
        (downloads / "countries/data.json").unlink()
    with pytest.raises(RuntimeError, match="complete a normal data release first"):
        receipt.assert_published_data(paths)


def test_legacy_verified_html_does_not_authorize_code_only_data(tmp_path):
    downloads, receipt, _ = _download_receipt(tmp_path)
    legacy = tmp_path / "exports/verified-pages-snapshot/release.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"release_id":"release-1"}')
    with pytest.raises(RuntimeError, match="Code-only release requires"):
        receipt.assert_published_data([downloads / "countries"])


@pytest.mark.parametrize('legacy', [False, True])
def test_editorial_changes_allow_code_only_without_relaxing_generated_data_checks(tmp_path, legacy):
    from src.services.data_release.stage_receipts import _inventory
    data = tmp_path / 'astro-site/src/data'
    data.mkdir(parents=True)
    notes = data / 'changelog.ts'
    notes.write_text('export const version = 1;')
    observations = data / 'meta.json'
    observations.write_text('{"cases":1}')
    receipt = StageReceipts(tmp_path, 'editorial-release', {}, {})
    identity = {'release_id': 'editorial-release'}
    receipt.mark_published(identity, data_paths=[data])
    if legacy:
        marker = tmp_path / 'exports/successful-site-release.json'
        payload = json.loads(marker.read_text())
        payload['data_snapshot']['files'] = _inventory([data])
        marker.write_text(json.dumps(payload))
    before = receipt.signature('ready', identity)
    notes.write_text('export const version = 2;')
    receipt.assert_published_data([data])
    assert receipt.signature('ready', identity) != before
    observations.write_text('{"cases":2}')
    with pytest.raises(RuntimeError, match='Code-only release requires'):
        receipt.assert_published_data([data])


def test_success_baseline_is_recorded_even_when_data_inventory_fails(tmp_path):
    _, receipt, _ = _download_receipt(tmp_path)
    with pytest.raises(RuntimeError, match="Published baseline saved"):
        receipt.mark_published({"release_id": "release-1"}, data_paths=[tmp_path / "missing"])
    marker = json.loads((tmp_path / "exports/successful-site-release.json").read_text())
    assert marker["release_id"] == "release-1"
    assert "data_snapshot" not in marker


def test_deploy_checkpoint_must_match_current_successful_task_and_artifact(tmp_path):
    _, receipt, _ = _download_receipt(tmp_path)
    identity = {"release_id": "release-1", "build_digest": "built-content"}
    assert not receipt.is_current_publication(identity)
    receipt.mark_published(identity)
    assert receipt.is_current_publication(identity)
    assert not receipt.is_current_publication({**identity, "build_digest": "other"})
    assert not receipt.is_current_publication({**identity, "release_id": "other"})
    assert not StageReceipts(tmp_path, "other-task", {}, {}).is_current_publication(identity)
    StageReceipts(tmp_path, "other-task", {}, {}).mark_published({"release_id": "release-2", "build_digest": "new-content"})
    assert not receipt.is_current_publication(identity)


def test_legacy_deploy_checkpoint_match_is_disabled_once_marker_exists(tmp_path):
    _, receipt, _ = _download_receipt(tmp_path)
    identity = {"release_id": "release-1", "build_digest": "built-content"}
    legacy = tmp_path / "exports/verified-pages-snapshot/release.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(identity))
    assert receipt.is_current_publication(identity)
    assert not receipt.is_current_publication({"release_id": "release-1"})
    receipt.mark_published({"release_id": "release-2", "build_digest": "new-content"})
    assert not receipt.is_current_publication(identity)
    (tmp_path / "exports/successful-site-release.json").write_text("{")
    assert not receipt.is_current_publication(identity)
