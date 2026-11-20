import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation.literature_publication import freeze_literature_json
from src.services.data_release import site_research_reuse as reuse
from src.services.data_release.papers_r2 import R2PapersConfig, R2S3Error
from src.services.data_release.surveillance_publish import capture_surveillance_baseline, _target


CONFIG = R2PapersConfig("account", "papers", "id", "secret", public_base_url="https://papers.test")
SETTINGS = SimpleNamespace(literature_json_enabled=True)


def put(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path
    put(root / "astro-site/src/pages/research/index.astro", "research template")
    put(root / "src/generation/site_data_literature.py", "projection implementation")
    source = root / "astro-site/public/site-data/research"
    put(source / "index.json", {"articles": [{"title": "Public article"}]})
    put(source / "catalogue.json", {"articles": [{"title": "Public article"}]})
    old = root / "exports/research-runs/published"
    pointer = freeze_literature_json(source, old, release_id="literature-published", generated_at="t")
    generation = "a" * 64
    pointer["papers"] = {"papers_generation_sha256": generation, "papers_manifest_sha256": "b" * 64,
        "papers_origin": CONFIG.public_base_url,
        "papers_manifest_url": f"{CONFIG.public_base_url}/releases/sha256/catalogues/{generation}.json"}
    put(old / "current.json", pointer)
    put(old / "published.json", {"release_id": pointer["release_id"], "public_verification": {"status": "verified"}})
    reuse.capture_render_inputs(root, old, settings=SETTINGS)
    put(root / "data/cache/isolated-research-publication.json", {"schema": 1, "keys": [], "identity": {
        "target": _target(CONFIG, reuse.PREFIX), "release_id": pointer["release_id"],
        "generation": generation, "origin": CONFIG.public_base_url}})
    put(root / "exports/research-current-receipt.json", {"schema": 1, "task_uuid": "published",
        "release_id": pointer["release_id"], "papers_generation_sha256": generation})
    assert reuse.record_committed_inputs(root, old, CONFIG)
    run = root / "exports/site-research-runs/new/release"
    body = (old / "current.json").read_bytes()
    capture_surveillance_baseline(run, CONFIG, prefix=reuse.PREFIX,
        client=SimpleNamespace(get_object=lambda _: body))
    reuse.capture_render_inputs(root, run, settings=SETTINGS)
    return root, run, source, body


def test_unchanged_generation_is_reused_and_remote_pointer_is_rechecked(prepared):
    root, run, source, body = prepared
    value = reuse.reusable_generation(root, run, CONFIG, research_source=source)
    assert value["identity"]["release_id"] == "literature-published"
    reuse.freeze_reuse(run, value)
    assert reuse.load_reuse(run, CONFIG) == value
    reuse.verify_reused_baseline(run, CONFIG, client=SimpleNamespace(get_object=lambda _: body))
    with pytest.raises(R2S3Error, match="baseline changed"):
        reuse.verify_reused_baseline(run, CONFIG, client=SimpleNamespace(get_object=lambda _: b"newer"))


@pytest.mark.parametrize("change", ["data", "template", "projection", "receipt", "current", "destination"])
def test_changed_or_missing_research_evidence_requires_publication(prepared, change):
    root, run, source, _ = prepared
    config = CONFIG
    if change == "data":
        put(source / "index.json", {"articles": [{"title": "Changed title"}]})
    elif change in {"template", "projection"}:
        path = root / ("astro-site/src/pages/research/index.astro" if change == "template"
                       else "src/generation/site_data_literature.py")
        put(path, "changed")
        (run / reuse.INPUTS).unlink()
        reuse.capture_render_inputs(root, run, settings=SETTINGS)
    elif change == "receipt":
        (root / reuse.COMMITTED).unlink()
    elif change == "current":
        (root / "exports/research-current-receipt.json").unlink()
    else:
        config = R2PapersConfig("account", "another", "id", "secret", public_base_url=CONFIG.public_base_url)
    assert reuse.reusable_generation(root, run, config, research_source=source) is None


def test_source_change_during_build_is_rejected(prepared):
    root, run, _, _ = prepared
    put(root / "astro-site/src/pages/research/index.astro", "changed during build")
    with pytest.raises(ValueError, match="inputs changed"):
        reuse.capture_render_inputs(root, run, settings=SETTINGS)


def test_recovery_does_not_depend_on_mutable_shared_receipt(prepared):
    root, run, source, _ = prepared
    value = reuse.reusable_generation(root, run, CONFIG, research_source=source)
    reuse.freeze_reuse(run, value)
    (root / reuse.COMMITTED).unlink()
    assert reuse.load_reuse(run, CONFIG) == value
    put(run / reuse.REUSED, {**value, "baseline_sha256": "0" * 64})
    with pytest.raises(ValueError, match="does not match"):
        reuse.load_reuse(run, CONFIG)


@pytest.mark.asyncio
async def test_only_selected_literature_markers_block_reuse(monkeypatch):
    from src.services.data_release import global_selection
    class Database:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    rows = [SimpleNamespace(id=7, line="knowledge")]
    read = AsyncMock(side_effect=lambda *_: rows)
    monkeypatch.setattr(global_selection, "read_selected_batch", read)
    assert not await reuse.selected_literature_changes({"publish_dirty_ids": [7]}, Database)
    rows[0].line = "literature"
    assert await reuse.selected_literature_changes({"publish_dirty_ids": [7]}, Database)
    assert await reuse.selected_literature_changes({}, Database)
    assert not await reuse.selected_literature_changes({"code_only": True}, Database)


@pytest.mark.asyncio
async def test_repository_publisher_skips_reused_papers_without_creating_upload(tmp_path):
    from src.services.data_release.repository_publishers import prepare_repository_publications
    manager = SimpleNamespace(add_workbook_entry=AsyncMock())
    service = SimpleNamespace(_run_logged_command=AsyncMock())
    result = await prepare_repository_publications(service=service,
        task=SimpleNamespace(task_uuid="task"),
        runtime=SimpleNamespace(root_dir=tmp_path, astro_dir=tmp_path / "astro-site", task_manager=manager),
        job=SimpleNamespace(job_id="site", include_git_push=False), python_path=Path("python"),
        git_env={}, branch="main", download_repo_url="", publish_commit_message="", download_git_timeout_seconds=1,
        literature_only=False, code_only=False, r2_papers_enabled=True, r2_reports_enabled=False,
        research_generation_reused={"identity": {"generation": "a" * 64}})
    assert result == []
    service._run_logged_command.assert_not_called()
    assert any(call.kwargs["metadata"].get("reason") == "unchanged_generation"
               for call in manager.add_workbook_entry.call_args_list)
