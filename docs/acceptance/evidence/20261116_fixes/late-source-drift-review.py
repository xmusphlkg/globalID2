import json
import runpy
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from src.generation import surveillance_data_export as dynamic
from src.generation import site_data_export
from src.core import database

_CONTEXT = runpy.run_path(str(Path.cwd() / 'tests/unit/test_surveillance_data_export.py'))['_context']

@pytest.mark.asyncio
async def test_late_freeze_source_drift_still_rejects_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(site_data_export, 'collect_site_export_context', AsyncMock(return_value=_CONTEXT()))
    db = type('Database', (), {'execute': AsyncMock(return_value=[])})()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(database, 'get_db', session)
    original_version = dynamic._generation_version
    stable_version = original_version()
    versions = iter([stable_version, stable_version, stable_version, 'changed-during-freeze'])
    monkeypatch.setattr(dynamic, '_generation_version', lambda: next(versions))
    state, public = tmp_path / 'private', tmp_path / 'public'
    with pytest.raises(ValueError, match='changed while freezing outputs'):
        await dynamic.export_surveillance_data(state, public, release_id='rejected-after-freeze')
    assert json.loads((public / 'current.json').read_text())['release_id'] == 'rejected-after-freeze'
    assert not (state / dynamic.COMPLETED_GENERATION).exists()
    monkeypatch.setattr(dynamic, '_generation_version', original_version)
    assert dynamic.completed_generation(state, public) is None
