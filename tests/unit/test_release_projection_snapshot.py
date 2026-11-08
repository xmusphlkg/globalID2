from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.generation import public_series_store
from src.generation.release_projection_snapshot import (
    load_projection_manifest,
    read_projection_country,
    write_projection_snapshot,
    validate_read_snapshot_projection,
)
from src.services.data_release import projection


def context():
    return {
        "incremental": True,
        "incremental_country_codes": ["JP"],
        "country_exports": [
            {"code": "JP", "country_data": {"disease_series": {"D001": {
                "dates": ["2026-01-01"], "cases": [7], "deaths": [0],
                "incidence_rates": [0.2], "period_granularity": "year",
            }}}},
            {"code": "US", "unchanged_curve": True, "country_data": {}},
        ],
    }


async def test_commit_uses_frozen_curves_without_reading_observations(tmp_path, monkeypatch):
    watermark = datetime(2026, 11, 1, tzinfo=timezone.utc)
    write_projection_snapshot(context(), tmp_path, {"JP": watermark})
    manifest = load_projection_manifest(tmp_path)
    assert set(manifest["countries"]) == {"JP"}
    assert read_projection_country(tmp_path, manifest, "JP")["source_high_water"] == watermark.isoformat()
    monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
        "countries_by_code": {"JP": {}, "US": {}}, "stored_disease_ids": {"D001"},
    }))
    raw_projection = AsyncMock(side_effect=AssertionError("must not reread observations"))
    replace = AsyncMock()
    monkeypatch.setattr(projection, "_publish_country", raw_projection)
    monkeypatch.setattr(public_series_store, "_replace_country_rows", replace)
    monkeypatch.setattr(projection, "_retire_disabled_publications", AsyncMock(return_value=set()))
    db = AsyncMock()
    await projection.commit_release_projection(
        db, task_uuid="release", country_codes=["JP"], full=False,
        dirty_ids=[7], prepared_dir=tmp_path,
    )
    raw_projection.assert_not_awaited()
    assert replace.await_args.args[2][0]["cases"] == [7]
    assert replace.await_args.kwargs["source_high_water"] == watermark
    db.execute.assert_awaited_once()


async def test_bad_snapshot_never_acknowledges_dirty_markers(tmp_path, monkeypatch):
    write_projection_snapshot(context(), tmp_path, {})
    (tmp_path / "jp.json").write_text('{}')
    monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
        "countries_by_code": {"JP": {}}, "stored_disease_ids": {"D001"},
    }))
    db = AsyncMock()
    with pytest.raises(ValueError, match="checksum"):
        await projection.commit_release_projection(
            db, task_uuid="release", country_codes=["JP"], full=False,
            dirty_ids=[7], prepared_dir=tmp_path,
        )
    db.execute.assert_not_awaited()


async def test_snapshot_scope_must_match_release(tmp_path, monkeypatch):
    write_projection_snapshot(context(), tmp_path, {})
    monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
        "countries_by_code": {"JP": {}, "US": {}},
    }))
    with pytest.raises(ValueError, match="scopes"):
        await projection.commit_release_projection(
            AsyncMock(), task_uuid="release", country_codes=["US"], full=False,
            dirty_ids=[], prepared_dir=tmp_path,
        )


async def test_precise_projection_does_not_expand_selected_country(tmp_path, monkeypatch):
    write_projection_snapshot(context(), tmp_path, {})
    monkeypatch.setattr(projection, "_load_publish_context", AsyncMock(return_value={
        "countries_by_code": {"JP": {}, "US": {}}, "stored_disease_ids": {"D001"},
    }))
    def unexpected_expansion(*args, **kwargs):
        raise AssertionError("An exact plan must not expand its scopes a second time")
    monkeypatch.setattr(projection, "expand_country_scopes", unexpected_expansion)
    replace = AsyncMock()
    monkeypatch.setattr(public_series_store, "_replace_country_rows", replace)
    monkeypatch.setattr(projection, "_retire_disabled_publications", AsyncMock(return_value=set()))
    await projection.commit_release_projection(
        AsyncMock(), task_uuid="exact", country_codes=["JP"], country_scopes_exact=True,
        full=False, dirty_ids=[7], prepared_dir=tmp_path,
    )
    replace.assert_awaited_once()
    assert replace.await_args.args[1] == "JP"


def test_concurrent_release_requires_snapshot_provenance_and_checksums(tmp_path, monkeypatch):
    from src.core.database_snapshot import SNAPSHOT_ENV
    write_projection_snapshot(context(), tmp_path, {})
    with pytest.raises(ValueError, match="snapshot identifier"):
        validate_read_snapshot_projection(tmp_path)
    monkeypatch.setenv(SNAPSHOT_ENV, "00000003-0000001B-1")
    write_projection_snapshot(context(), tmp_path, {})
    assert validate_read_snapshot_projection(tmp_path)["database_snapshot"] == "00000003-0000001B-1"
    (tmp_path / "jp.json").write_text('{}')
    with pytest.raises(ValueError, match="checksum"):
        validate_read_snapshot_projection(tmp_path)


@pytest.mark.parametrize("options,match", [
    ({"prepared_dir": None}, "frozen publication projection"),
    ({"prepared_dir": Path("unused"), "marked_before": "2026-11-01T00:00:00Z"}, "exact dirty IDs"),
])
async def test_concurrent_release_never_falls_back_to_live_projection(options, match):
    db = AsyncMock()
    with pytest.raises(ValueError, match=match):
        await projection.commit_release_projection(
            db, task_uuid="release", country_codes=["JP"], full=False,
            dirty_ids=[1], require_prepared=True, **options,
        )
    db.execute.assert_not_awaited()
