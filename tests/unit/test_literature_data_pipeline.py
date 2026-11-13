from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import Task
from src.domain.collection import PublishDirty
from src.services.data_release import data_pipeline
from tests.unit.test_release_dirty_selection import release_db


@pytest.mark.parametrize('planned', [False, True])
async def test_literature_release_retries_frozen_candidate_and_acks_only_selected_ids(release_db, tmp_path, monkeypatch, planned):
    maker, service, _ = release_db
    async with maker() as db:
        rows = [PublishDirty(line=line, scope_key='*', source_task_uuid='source', marked_at=datetime.now(timezone.utc))
                for line in ('literature', 'literature', 'situation')]
        db.add_all(rows)
        await db.commit()
        ids = [row.id for row in rows]
    @asynccontextmanager
    async def database():
        async with maker() as db:
            yield db
    snapshots = []
    @asynccontextmanager
    async def snapshot():
        snapshots.append(True)
        yield {'TEST_SNAPSHOT': 'frozen'}
    runtime = SimpleNamespace(root_dir=tmp_path, astro_dir=tmp_path / 'astro-site', get_database=database,
        read_snapshot=snapshot, task_model=Task, generate_timeout_seconds=60, astro_build_timeout_seconds=60,
        download_publish_timeout_seconds=60,
        task_manager=SimpleNamespace(update_task_progress=AsyncMock(), add_workbook_entry=AsyncMock()))
    task = SimpleNamespace(task_uuid='research-test', input_data={'data_only': True, 'data_line': 'literature',
                                                                  'publish_dirty_ids': [ids[0]]})
    if planned:
        task.input_data['publication_plan'] = {'plan_id': 'plan', 'plan_digest': 'digest'}
    commands = []
    fail = True
    async def command(_uuid, **kwargs):
        nonlocal fail
        cmd = kwargs['cmd']
        commands.append(cmd)
        if '--capture-baseline-only' in cmd:
            assert not snapshots
        elif 'scripts/capture_research_context.py' in cmd:
            (tmp_path / 'exports/research-runs/research-test/context.json').write_text('{}')
        elif 'scripts/export_research_snapshot.py' in cmd:
            assert kwargs['env']['TEST_SNAPSHOT'] == 'frozen'
            target = tmp_path / 'exports/research-runs/research-test/snapshot'
            target.mkdir()
            (target / 'article-inputs.json').write_text('{}')
        elif 'scripts/build-research.mjs' in cmd:
            stage = tmp_path / 'exports/research-stage-research-test'
            stage.mkdir(parents=True)
            stage.with_name(stage.name + '.inputs.json').write_text('{}')
        elif 'scripts/publish_research_candidate.py' in cmd:
            if fail:
                fail = False
                raise RuntimeError('upload failed')
            if '--prepare-only' in cmd:
                (tmp_path / 'exports/research-runs/research-test/papers-prepared.json').write_text('{}')
                return
            (tmp_path / 'exports/research-runs/research-test/current.json').write_text('{}')
            (tmp_path / 'exports/research-runs/research-test/published.json').write_text(json.dumps({
                'release_id': 'literature-research-test', 'papers': {'generation_sha256': 'a' * 64}, 'json': {}}))
        else:
            raise AssertionError(cmd)
    monkeypatch.setattr(service, '_run_logged_command', command)
    monkeypatch.setattr(service, '_python_executable', lambda: 'python')
    monkeypatch.setattr(service, '_cloudflare_account_id', lambda: 'account')
    monkeypatch.setattr(service, '_cloudflare_api_token', lambda: 'token')
    with pytest.raises(RuntimeError, match='upload failed'):
        await data_pipeline.execute_data_release(service, task, runtime=runtime)
    # A process launch failure before complete Papers preparation must remain
    # retryable, rather than becoming an impossible recovery-only publication.
    assert not (tmp_path / 'exports/publication-recovery/research-test.json').exists()
    async with maker() as db:
        assert (await db.get(PublishDirty, ids[0])).published_at is None
    result = await data_pipeline.execute_data_release(service, task, runtime=runtime)
    assert result['release_id'] == 'literature-research-test'
    assert len(snapshots) == 1
    assert sum('scripts/build-research.mjs' in cmd for cmd in commands) == 1
    assert sum('scripts/capture_research_context.py' in cmd for cmd in commands) == 1
    async with maker() as db:
        assert (await db.get(PublishDirty, ids[0])).published_task_uuid == task.task_uuid
        assert (await db.get(PublishDirty, ids[1])).published_at is None
        assert (await db.get(PublishDirty, ids[2])).published_at is None
