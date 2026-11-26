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


def test_explicit_preparation_archives_interrupted_build_after_renderer_change(prepared):
    root, run, _, _ = prepared
    stage = root / "exports/research-stage-interrupted"
    put(stage / "partial.html", "incomplete old output")
    put(run / "snapshot/article-inputs.json", "frozen facts")
    put(run / "context.json", "frozen public context")
    put(run / "projection-build-baseline.json", {"schema": 1, "old": True})
    put(run / "template-baseline/candidate.json", "old template receipt")
    original = (run / reuse.INPUTS).read_bytes()
    baseline = (run / ".publication/starting-baseline.json").read_bytes()
    put(root / "astro-site/src/pages/research/index.astro", "fixed renderer")

    current = reuse.prepare_render_inputs(root, run, stage=stage, settings=SETTINGS)

    assert not stage.exists()
    assert not (run / "projection-build-baseline.json").exists()
    archive, = (run / "build-recovery").iterdir()
    assert (archive / reuse.INPUTS).read_bytes() == original
    assert (archive / "stage/partial.html").read_text() == "incomplete old output"
    assert (archive / "template-baseline/candidate.json").read_text() == "old template receipt"
    assert json.loads((archive / "recovery.json").read_text())["reason"] == "renderer_inputs_changed"
    assert (run / "snapshot/article-inputs.json").read_text() == "frozen facts"
    assert (run / "context.json").read_text() == "frozen public context"
    assert (run / ".publication/starting-baseline.json").read_bytes() == baseline
    assert reuse.capture_render_inputs(root, run, settings=SETTINGS) == current


@pytest.mark.parametrize("marker", ["ready.json", "papers-prepared.json", "current.json", "authority-committed.json",
    "published.json", "research-reused.json", "papers-build-manifest.json", "projection-v3"])
def test_explicit_preparation_never_rewrites_frozen_or_committed_candidates(prepared, marker):
    root, run, _, _ = prepared
    stage = root / "exports/research-stage-frozen"
    put(stage / "partial.html", "old output")
    put(run / marker, "frozen candidate evidence")
    original = (run / reuse.INPUTS).read_bytes()
    put(root / "astro-site/src/pages/research/index.astro", "fixed renderer")
    with pytest.raises(ValueError, match="Frozen research candidate"):
        reuse.prepare_render_inputs(root, run, stage=stage, settings=SETTINGS)
    assert (run / reuse.INPUTS).read_bytes() == original
    assert (stage / "partial.html").read_text() == "old output"
    assert not (run / "build-recovery").exists()


def test_explicit_preparation_cannot_replace_complete_build(prepared):
    root, run, _, _ = prepared
    stage = root / "exports/research-stage-complete"
    put(stage / "index.html", "complete output")
    put(Path(f"{stage}.inputs.json"), {"schema": 1})
    with pytest.raises(ValueError, match="Completed research build"):
        reuse.prepare_render_inputs(root, run, stage=stage, settings=SETTINGS)


def test_preparation_rejects_snapshot_as_stage_before_moving_frozen_facts(prepared):
    root, run, _, _ = prepared
    stage = run / "snapshot"
    put(stage / "article-inputs.json", "frozen facts")
    original = (run / reuse.INPUTS).read_bytes()
    put(root / "astro-site/src/pages/research/index.astro", "fixed renderer")
    with pytest.raises(ValueError, match="paths overlap"):
        reuse.prepare_render_inputs(root, run, stage=stage, settings=SETTINGS)
    assert (stage / "article-inputs.json").read_text() == "frozen facts"
    assert (run / reuse.INPUTS).read_bytes() == original
    assert not (run / "build-recovery").exists()


def test_prepare_build_subprocess_path_refreshes_unfrozen_renderer_contract(prepared, monkeypatch):
    from scripts.publish_research_candidate import publish

    root, run, _, _ = prepared
    monkeypatch.setenv("GIDS_PAPERS_PROJECTION_V3", "1")
    put(root / "src/generation/site_data_literature.py", "fixed projection implementation")
    result = publish(run=run, snapshot=run / "snapshot", stage=root / "exports/research-stage-retry",
        shared=root / "exports/research-json", receipt=root / "data/cache/papers.json",
        release_id="literature-retry", config=CONFIG, prepare_build=True)
    assert result["schema"] == 1
    assert (run / "projection-build-baseline.json").is_file()
    assert reuse.capture_render_inputs(root, run) is not None
    assert len(list((run / "build-recovery").iterdir())) == 1


def test_pages_only_retains_remote_papers_after_renderer_changes(prepared):
    root, run, source, body = prepared
    (root / reuse.COMMITTED).unlink()
    value = reuse.retained_generation(run, CONFIG, source)
    reuse.freeze_reuse(run, value)
    assert reuse.load_reuse(run, CONFIG)["identity"]["release_id"] == "literature-published"
    reuse.verify_reused_baseline(run, CONFIG, client=SimpleNamespace(get_object=lambda _: body))
    put(source / "index.json", {"articles": []})
    with pytest.raises(ValueError, match="published baseline"):
        reuse.retained_generation(run, CONFIG, source)


def test_pages_only_request_requires_explicit_template():
    from dashboard.api.schemas.publication import PublicationPlanCreate

    assert PublicationPlanCreate(kind="template", main_site_only=True).main_site_only
    for kind in ("full", "auto", "content"):
        with pytest.raises(ValueError, match="requires a template"):
            PublicationPlanCreate(kind=kind, main_site_only=True)


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
