"""Independent literature export, build and fenced publication task."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from src.generation.literature_publication import _atomic_write
from .global_selection import acknowledge_global_selection, validate_global_selection


def _ready(run: Path, *, task_uuid: str, dirty_ids: list[int]) -> bool:
    path = run / 'ready.json'
    if not path.exists():
        return False
    value = json.loads(path.read_bytes())
    if value != {'schema': 1, 'task_uuid': task_uuid, 'dirty_ids': dirty_ids}:
        raise ValueError('Frozen literature selection changed')
    return True


async def execute_literature_data_release(service, task, *, runtime) -> dict:
    data = dict(task.input_data or {})
    dirty_ids = sorted(data.get('publish_dirty_ids') or [])
    if data.get('incremental_country_codes'):
        raise ValueError('Literature publication does not accept surveillance scopes')
    if not isinstance(task.task_uuid, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', task.task_uuid):
        raise ValueError('Invalid literature release task UUID')
    started = time.monotonic()
    async with runtime.get_database() as db:
        await validate_global_selection(db, dirty_ids, line='literature', task_uuid=task.task_uuid)
    root = runtime.root_dir
    run = root / 'exports/research-runs' / task.task_uuid
    snapshot, context = run / 'snapshot', run / 'context.json'
    stage = root / 'exports' / f'research-stage-{task.task_uuid}'
    build_receipt = Path(f'{stage}.inputs.json')
    release_id = f'literature-{task.task_uuid}'
    python = service._python_executable()
    config = service._config()
    env = {'CLOUDFLARE_ACCOUNT_ID': service._cloudflare_account_id(),
        'CLOUDFLARE_API_TOKEN': service._cloudflare_api_token(),
        'R2_PAPERS_BUCKET': str(getattr(config, 'r2_papers_bucket', 'globalid-papers')),
        'R2_PAPERS_PUBLIC_BASE_URL': str(getattr(config, 'r2_papers_public_base_url', 'https://papers.globalinfectiousdisease.com')),
        'R2_ACCESS_KEY_ID': str(getattr(config, 'r2_access_key_id', '')),
        'R2_SECRET_ACCESS_KEY': str(getattr(config, 'r2_secret_access_key', '')),
        'R2_ENDPOINT_URL': str(getattr(config, 'r2_endpoint_url', '')),
        'R2_ACCOUNT_ID': str(getattr(config, 'r2_account_id', '') or service._cloudflare_account_id())}
    run.mkdir(parents=True, exist_ok=True)
    build_started = time.monotonic()
    if not await asyncio.to_thread(_ready, run, task_uuid=task.task_uuid, dirty_ids=dirty_ids):
        await service._run_logged_command(task.task_uuid, title='Capture Literature Publication Baseline',
            cmd=[str(python), 'scripts/publish_surveillance_data.py', '--source-dir', str(run), '--prefix',
                 'site-data/research-publication', '--capture-baseline-only'], cwd=root, env=env,
            metadata={'event': 'literature_publication_baseline'}, timeout_seconds=runtime.download_publish_timeout_seconds)
        if not context.is_file():
            await service._run_logged_command(task.task_uuid, title='Capture Published Research Context',
                cmd=[str(python), 'scripts/capture_research_context.py', '--output', str(context)], cwd=root, env=env,
                metadata={'event': 'literature_publication_context'}, timeout_seconds=runtime.generate_timeout_seconds)
        if not (snapshot / 'article-inputs.json').is_file():
            if snapshot.is_symlink():
                raise ValueError('Invalid literature snapshot directory')
            if snapshot.exists():
                await asyncio.to_thread(shutil.rmtree, snapshot)
            async with runtime.read_snapshot() as snapshot_env:
                await service._run_logged_command(task.task_uuid, title='Export Literature Snapshot',
                    cmd=[str(python), 'scripts/export_research_snapshot.py', '--context', str(context), '--candidate', str(snapshot)],
                    cwd=root, env={**snapshot_env, 'PYTHONUNBUFFERED': '1'}, metadata={'event': 'literature_publication_export'},
                    timeout_seconds=runtime.generate_timeout_seconds)
        await runtime.task_manager.update_task_progress(task.task_uuid, 30)
        if not (stage.is_dir() and build_receipt.is_file()):
            if stage.is_symlink():
                raise ValueError('Invalid literature stage directory')
            if stage.exists():
                await asyncio.to_thread(shutil.rmtree, stage)
            await service._run_logged_command(task.task_uuid, title='Build Literature Pages',
                cmd=['node', 'scripts/build-research.mjs'], cwd=runtime.astro_dir,
                env={'GIDS_RESEARCH_SNAPSHOT_DIR': str(snapshot), 'GIDS_RESEARCH_BUILD_OUTPUT': str(stage),
                     'NODE_OPTIONS': '--max-old-space-size=12288'},
                metadata={'event': 'literature_publication_build'}, timeout_seconds=max(runtime.astro_build_timeout_seconds, runtime.generate_timeout_seconds))
        await asyncio.to_thread(_atomic_write, run / 'ready.json', json.dumps(
            {'schema': 1, 'task_uuid': task.task_uuid, 'dirty_ids': dirty_ids}, separators=(',', ':')).encode())
    async with runtime.get_database() as db:
        await validate_global_selection(db, dirty_ids, line='literature', task_uuid=task.task_uuid)
    await runtime.task_manager.update_task_progress(task.task_uuid, 65)
    publish_started = time.monotonic()
    await service._run_logged_command(task.task_uuid, title='Publish Literature Generation',
        cmd=[str(python), 'scripts/publish_research_candidate.py', '--run', str(run), '--snapshot', str(snapshot),
             '--stage', str(stage), '--release-id', release_id, '--workers', '32'], cwd=root, env=env,
        metadata={'event': 'literature_publication_publish'}, timeout_seconds=max(runtime.download_publish_timeout_seconds, 3 * 60 * 60))
    published = json.loads((run / 'published.json').read_bytes())
    if published.get('release_id') != release_id:
        raise ValueError('Published literature receipt belongs to a different task')
    async with runtime.get_database() as db:
        await acknowledge_global_selection(db, dirty_ids, line='literature', task_uuid=task.task_uuid)
        result = {'data_only': True, 'data_line': 'literature', 'release_id': release_id,
            'publish_dirty_count': len(dirty_ids), 'papers': published['papers'], 'json': published['json'],
            'manifest_sha256': hashlib.sha256((run / 'current.json').read_bytes()).hexdigest(),
            'release_timing': {'build_seconds': round(publish_started - build_started, 3),
                'publish_and_ack_seconds': round(time.monotonic() - publish_started, 3),
                'elapsed_seconds': round(time.monotonic() - started, 3)}, 'pages_deployed': False}
        if getattr(task, 'id', None) is not None:
            stored = await db.get(runtime.task_model, task.id)
            if stored is not None:
                stored.output_data = result
        await db.commit()
    receipt = {'schema': 1, 'task_uuid': task.task_uuid, 'release_id': release_id,
        'papers_generation_sha256': published['papers']['generation_sha256']}
    await asyncio.to_thread(_atomic_write, root / 'exports/research-current-receipt.json',
                            json.dumps(receipt, separators=(',', ':')).encode())
    await runtime.task_manager.add_workbook_entry(task.task_uuid, entry_type='info', title='Literature Published',
        content=json.dumps(result), content_type='json', metadata={'event': 'literature_publication_completed', **result})
    return result
