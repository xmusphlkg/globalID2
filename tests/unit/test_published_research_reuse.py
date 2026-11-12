import json
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation.published_research_reuse import install_published_research


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
