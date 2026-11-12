import json
import hashlib
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation.published_research_reuse import (
    install_published_research, prepare_research_for_baseline, require_public_research_matches,
)


def snapshot(root: Path, task_id: str) -> Path:
    source = root / "exports/research-runs" / task_id / "snapshot"
    for relative, value in {
        "build/research/routes.json": {"articles": [{"slug": "new"}]},
        "build/research/index.json": {"articles": [{"title": "new"}]},
        "public/research/index.json": {"version": "new"},
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    receipt = root / "exports/research-current-receipt.json"
    receipt.write_text(json.dumps({"task_uuid": task_id, "release_id": f"literature-{task_id}"}))
    return source


def test_installs_one_published_snapshot_without_mixing_old_files(tmp_path):
    task_id = "f7d086df-374e-4a86-b216-11f4265c1f42"
    snapshot(tmp_path, task_id)
    output, public = tmp_path / "site", tmp_path / "public"
    for target in (output, public):
        (target / "research").mkdir(parents=True)
        (target / "research/old.json").write_text("{}")
    assert install_published_research(tmp_path, output, public) == f"literature-{task_id}"
    assert json.loads((output / "research/routes.json").read_text())["articles"][0]["slug"] == "new"
    assert json.loads((public / "research/index.json").read_text())["version"] == "new"
    assert not (output / "research/old.json").exists()


def test_missing_snapshot_does_not_touch_current_outputs(tmp_path):
    task_id = "f7d086df-374e-4a86-b216-11f4265c1f42"
    source = snapshot(tmp_path, task_id)
    (source / "public/research/index.json").unlink()
    output, public = tmp_path / "site", tmp_path / "public"
    (output / "research").mkdir(parents=True)
    (output / "research/old.json").write_text("{}")
    with pytest.raises(ValueError, match="missing"):
        install_published_research(tmp_path, output, public)
    assert (output / "research/old.json").exists()


def test_failed_second_swap_restores_both_previous_trees(tmp_path, monkeypatch):
    from src.generation import published_research_reuse as reuse

    task_id = "f7d086df-374e-4a86-b216-11f4265c1f42"
    snapshot(tmp_path, task_id)
    output, public = tmp_path / "site", tmp_path / "public"
    for target in (output, public):
        (target / "research").mkdir(parents=True)
        (target / "research/old.json").write_text("{}")
    replace = reuse.os.replace

    def fail_second_install(source, destination):
        if Path(destination) == public / "research" and Path(source).name.startswith(".research-stage-"):
            raise OSError("simulated failed install")
        return replace(source, destination)

    monkeypatch.setattr(reuse.os, "replace", fail_second_install)
    with pytest.raises(OSError, match="simulated"):
        install_published_research(tmp_path, output, public)
    assert (output / "research/old.json").is_file()
    assert (public / "research/old.json").is_file()


def bound_snapshot(root, task_id='f7d086df-374e-4a86-b216-11f4265c1f42'):
    source = snapshot(root, task_id)
    (source / 'public/research/catalogue.json').write_text('{"papers":1}')
    (source / 'build/research/articles').mkdir()
    article = b'{"slug":"new","findings":"new evidence"}'
    (source / 'build/research/articles/new.json').write_bytes(article)
    (source / 'article-inputs.json').write_text(json.dumps({'schema': 1, 'articles': {'new': hashlib.sha256(article).hexdigest()}}))
    receipt_path = root / 'exports/research-current-receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt['papers_generation_sha256'] = 'a' * 64
    receipt_path.write_text(json.dumps(receipt))
    files = {}
    for name in ('index.json', 'catalogue.json'):
        raw = (source / 'public/research' / name).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        files[name] = {'url': f'objects/{sha}.json', 'sha256': sha, 'bytes': len(raw)}
    baseline = {'schema_version': 1, 'release_id': receipt['release_id'], 'files': files,
                'papers': {'papers_generation_sha256': receipt['papers_generation_sha256']}}
    return source, baseline


def test_style_build_installs_newest_published_private_snapshot_instead_of_old_static_export(tmp_path):
    source, baseline = bound_snapshot(tmp_path)
    output, public = tmp_path / 'site', tmp_path / 'public'
    for base in (output, public):
        (base / 'research').mkdir(parents=True)
        (base / 'research/index.json').write_text('{"version":"old"}')
    (output / 'research/routes.json').write_text('{"articles":[{"slug":"old"}]}')
    (public / 'research/catalogue.json').write_text('{}')
    assert prepare_research_for_baseline(tmp_path, output, public, baseline) == baseline['release_id']
    assert (output / 'research/routes.json').read_bytes() == (source / 'build/research/routes.json').read_bytes()
    require_public_research_matches(public / 'research', baseline)


def test_following_style_release_can_reuse_same_literature_snapshot_under_new_site_identity(tmp_path):
    _, baseline = bound_snapshot(tmp_path)
    baseline['release_id'] = 'site-after-literature'
    baseline['papers']['papers_generation_sha256'] = 'b' * 64
    output, public = tmp_path / 'site', tmp_path / 'public'
    assert prepare_research_for_baseline(tmp_path, output, public, baseline).startswith('literature-')
    require_public_research_matches(public / 'research', baseline)


def test_receipt_generation_mismatch_rejects_even_when_public_index_happens_to_match(tmp_path):
    _, baseline = bound_snapshot(tmp_path)
    baseline['papers']['papers_generation_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='captured publication generation'):
        prepare_research_for_baseline(tmp_path, tmp_path / 'site', tmp_path / 'public', baseline)
    assert not (tmp_path / 'site/research').exists()


def test_existing_matching_site_sources_need_no_receipt_or_database(tmp_path):
    source, baseline = bound_snapshot(tmp_path)
    baseline['release_id'] = 'site-current'
    output, public = tmp_path / 'site', tmp_path / 'public'
    import shutil
    shutil.copytree(source / 'build/research', output / 'research')
    shutil.copytree(source / 'public/research', public / 'research')
    (tmp_path / 'exports/research-current-receipt.json').unlink()
    marker = (output / 'research/routes.json').stat().st_mtime_ns
    assert prepare_research_for_baseline(tmp_path, output, public, baseline) == baseline['release_id']
    assert (output / 'research/routes.json').stat().st_mtime_ns == marker


def test_stale_local_export_without_matching_snapshot_cannot_replace_newer_literature(tmp_path):
    source, baseline = bound_snapshot(tmp_path)
    baseline['release_id'] = 'site-current'
    output, public = tmp_path / 'site', tmp_path / 'public'
    import shutil
    shutil.copytree(source / 'build/research', output / 'research')
    shutil.copytree(source / 'public/research', public / 'research')
    (public / 'research/index.json').write_text('{"version":"old"}')
    (tmp_path / 'exports/research-current-receipt.json').unlink()
    with pytest.raises(ValueError, match='differs from the published baseline'):
        prepare_research_for_baseline(tmp_path, output, public, baseline)


def test_snapshot_changed_during_copy_is_rejected_before_either_tree_is_swapped(tmp_path, monkeypatch):
    import subprocess
    _, baseline = bound_snapshot(tmp_path)
    output, public = tmp_path / 'site', tmp_path / 'public'
    for base in (output, public):
        (base / 'research').mkdir(parents=True)
        (base / 'research/old.json').write_text('{}')
    run = subprocess.run
    def copy_then_mutate(command, **kwargs):
        result = run(command, **kwargs)
        stage = Path(command[-1])
        if (stage / 'catalogue.json').exists():
            (stage / 'index.json').write_text('{"changed":true}')
        return result
    monkeypatch.setattr(subprocess, 'run', copy_then_mutate)
    with pytest.raises(ValueError, match='differs from the published baseline'):
        install_published_research(tmp_path, output, public, expected_manifest=baseline)
    assert (output / 'research/old.json').exists()
    assert (public / 'research/old.json').exists()


def test_preserving_research_requires_complete_baseline_and_exact_shard_selection(tmp_path):
    source, baseline = bound_snapshot(tmp_path)
    with pytest.raises(ValueError, match='explicit migration'):
        require_public_research_matches(source / 'public/research', {'schema_version': 1, 'release_id': 'migration', 'files': {}})
    baseline['files']['shards/missing.json'] = {'url': 'objects/' + 'a' * 64 + '.json', 'sha256': 'a' * 64, 'bytes': 1}
    with pytest.raises(ValueError, match='file selection'):
        require_public_research_matches(source / 'public/research', baseline)


def test_matching_public_projection_cannot_hide_changed_private_article(tmp_path):
    source, baseline = bound_snapshot(tmp_path)
    (source / 'build/research/articles/new.json').write_text('{"findings":"wrong private body"}')
    with pytest.raises(ValueError, match='private article differs'):
        prepare_research_for_baseline(tmp_path, tmp_path / 'site', tmp_path / 'public', baseline)
    assert not (tmp_path / 'site/research').exists()


def test_unacknowledged_literature_pointer_requires_private_snapshot_receipt(tmp_path):
    _, baseline = bound_snapshot(tmp_path)
    (tmp_path / 'exports/research-current-receipt.json').unlink()
    with pytest.raises(ValueError, match='no acknowledged private snapshot'):
        prepare_research_for_baseline(tmp_path, tmp_path / 'site', tmp_path / 'public', baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True])
async def test_release_requests_snapshot_reuse_only_for_detached_literature(tmp_path, detached):
    from src.services.data_release.input_snapshot import generate_release_inputs

    @asynccontextmanager
    async def read_snapshot():
        yield {"GLOBALID_RELEASE_DB_SNAPSHOT": "snapshot"}

    commands = []
    async def run(_uuid, *, title, cmd, **_kwargs):
        commands.append((title, cmd))

    service = SimpleNamespace(
        _run_logged_command=run,
        _update_situation_notices_command=lambda **_: ["situation"],
        _generate_site_data_command=lambda **_: ["generate"],
    )
    runtime = SimpleNamespace(
        root_dir=tmp_path, astro_dir=tmp_path, read_snapshot=read_snapshot,
        generate_timeout_seconds=60,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()),
    )
    await generate_release_inputs(
        service=service, task=SimpleNamespace(task_uuid="task"), runtime=runtime,
        job=SimpleNamespace(job_id="site"), python_path=Path("python"), git_env={},
        download_url_base="", input_data={"content_only": True, "literature_detached": detached},
    )
    command = next(cmd for title, cmd in commands if title == "Generate Site Data")
    assert "--content-only" in command
    assert ("--reuse-published-research" in command) is detached
