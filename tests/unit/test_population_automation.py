"""Exercise source isolation and durable publication retries, without network."""
import csv
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import update_population_automatically as scheduler


NOW = '2026-11-16T11:00:00+08:00'


def source_runner(*, failures=(), changes=()):
    def run(command, **kwargs):
        code = command[command.index('--sources') + 1]
        if code in failures:
            return SimpleNamespace(returncode=1, stdout='', stderr='official source unavailable')
        output = Path(command[command.index('--output') + 1])
        snapshot = output / 'snapshot'
        snapshot.mkdir(parents=True, exist_ok=True)
        with (snapshot / 'population.csv').open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=['country_code', 'year', 'population', 'source', 'reference_date', 'source_url', 'basis'])
            writer.writeheader()
            writer.writerow(dict(country_code=code + '-X', year=2025, population=100, source=code, reference_date='2025-12-31', source_url='https://official.example', basis='resident'))
        applied = '--apply' in command
        scheduler.write_json(output / ('latest.json' if applied else 'latest_plan.json'), {
            'applied': applied, 'snapshot': str(snapshot), 'rows': 1,
            'coverage': {code + '-X': {'last_year': 2025}}, 'sources': {'live': {'url': 'https://official.example'}},
            'changes': {'changed_rows': int(code in changes), 'changed_country_codes': [code + '-X'] if code in changes else []},
        })
        assert '--export' not in command  # only one release request per batch
        return SimpleNamespace(returncode=0, stdout='validated', stderr='')
    return run


def test_one_failed_source_does_not_block_other_updates_or_discard_prior_population(tmp_path, monkeypatch):
    release = AsyncMock(return_value={'status': 'queued'})
    monkeypatch.setattr(scheduler, 'queue_population_release', release)
    scheduler.refresh_sources(output=tmp_path, sources=['CN', 'AU', 'BR'], apply=True, queue_release=True, runner=source_runner(), now=NOW)
    result = scheduler.refresh_sources(output=tmp_path, sources=['BR', 'CN', 'AU'], apply=True, queue_release=True, runner=source_runner(failures=['BR'], changes=['CN', 'AU']), now=NOW + '1')
    assert result['failed_sources'] == ['BR']
    assert result['sources']['BR']['last_success_at'] == NOW
    assert result['sources']['BR']['consecutive_failures'] == 1
    assert result['sources']['AU']['status'] == 'healthy'
    assert result['changed_country_codes'] == ['AU-X', 'CN-X']
    release.assert_awaited_once()
    receipt = scheduler.read_json(tmp_path / 'latest.json', {})
    assert receipt['locations'] == 3
    assert not result['release_pending']


def test_failed_publication_retries_even_when_next_source_check_is_unchanged(tmp_path, monkeypatch):
    release = AsyncMock(side_effect=RuntimeError('queue unavailable'))
    monkeypatch.setattr(scheduler, 'queue_population_release', release)
    result = scheduler.refresh_sources(output=tmp_path, sources=['CN'], apply=True, queue_release=True, runner=source_runner(changes=['CN']), now=NOW)
    assert result['release_pending'] and result['failed_sources'] == ['publication']
    release.side_effect = None
    release.return_value = {'status': 'queued'}
    result = scheduler.refresh_sources(output=tmp_path, sources=['CN'], apply=True, queue_release=True, runner=source_runner(), now=NOW + '1')
    assert release.await_count == 2
    assert result['changed_country_codes'] == []
    assert not result['release_pending']


def test_pending_release_survives_mid_batch_interruption(tmp_path):
    scheduler.write_json(tmp_path / 'scheduler-status.json', {'release_pending': True})
    runner = source_runner()
    def interrupted(command, **kwargs):
        if command[command.index('--sources') + 1] == 'AU':
            raise KeyboardInterrupt
        return runner(command, **kwargs)
    import pytest
    with pytest.raises(KeyboardInterrupt):
        scheduler.refresh_sources(output=tmp_path, sources=['CN', 'AU'], apply=True, queue_release=False, runner=interrupted, now=NOW)
    assert scheduler.read_json(tmp_path / 'scheduler-status.json', {})['release_pending']


def test_dry_run_does_not_modify_applied_receipt_or_queue_publication(tmp_path, monkeypatch):
    scheduler.write_json(tmp_path / 'latest.json', {'untouched': True})
    release = AsyncMock()
    monkeypatch.setattr(scheduler, 'queue_population_release', release)
    scheduler.refresh_sources(output=tmp_path, sources=['CN'], apply=False, queue_release=False, runner=source_runner(), now=NOW)
    assert scheduler.read_json(tmp_path / 'latest.json', {}) == {'untouched': True}
    release.assert_not_called()


def test_scheduler_recovers_verified_snapshots_after_moving_the_data_tree(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    scheduler.refresh_sources(output=old, sources=['CN'], apply=True, queue_release=False,
                              runner=source_runner(), now=NOW)
    state_path = old / "scheduler-status.json"
    state = scheduler.read_json(state_path, {})
    source_csv = Path(state["sources"]["CN"]["last_success_snapshot"]) / "population.csv"
    state["sources"]["CN"]["last_success_sha256"] = hashlib.sha256(source_csv.read_bytes()).hexdigest()
    scheduler.write_json(state_path, state)
    shutil.move(old, new)

    scheduler.refresh_sources(output=new, sources=['CN'], apply=True, queue_release=False,
                              runner=source_runner(failures=['CN']), now=NOW + '1')
    receipt = scheduler.read_json(new / "latest.json", {})
    assert receipt["locations"] == 1
    assert receipt["source_statuses"]["CN"]["status"] == "failed"

    relocated = new / "sources" / "cn" / "snapshot" / "population.csv"
    relocated.write_bytes(b"mismatched")
    with pytest.raises(ValueError, match="Cannot verify relocated population snapshot"):
        scheduler.refresh_sources(output=new, sources=['CN'], apply=True, queue_release=False,
                                  runner=source_runner(failures=['CN']), now=NOW + '2')
    assert scheduler.read_json(new / "latest.json", {}) == receipt


async def test_changed_population_requests_surveillance_data_release_only(monkeypatch):
    from src.core import database
    from src.services.data_release_service import data_release_service
    release = AsyncMock(return_value={'status': 'queued'})
    dispose = AsyncMock()
    monkeypatch.setattr(data_release_service, 'trigger_job', release)
    monkeypatch.setattr(database, 'dispose_database', dispose)
    assert await scheduler.queue_population_release() == {'status': 'queued'}
    release.assert_awaited_once_with(
        'site-release', manual=True, trigger='official-population-scheduled',
        data_only=True, data_line='surveillance', suppress_subscription_dispatch=True,
    )
    dispose.assert_awaited_once()
