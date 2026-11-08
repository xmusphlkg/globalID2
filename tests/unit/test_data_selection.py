from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import Base, Country
from src.domain.collection import PublishDirty
from src.services.data_release import data_selection


@pytest.fixture
async def selection_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'selection.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Country.__table__, PublishDirty.__table__],
        ))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(data_selection, "get_country_bootstrap_config", lambda code: {
        "public_release_enabled": code != "CA",
    })
    async with sessions() as db:
        db.add_all([Country(code=code, name=code, name_en=code, is_active=active) for code, active in [
            ("US", True), ("US-AK", True), ("US-CA", False), ("JP", True), ("CA", True),
        ]])
        await db.commit()
    try:
        yield sessions
    finally:
        await engine.dispose()


async def marker(db, scope="US", *, precise=True, line="surveillance", published=False):
    row = PublishDirty(line=line, scope_key=scope, source_task_uuid="collection",
        marked_at=datetime.now(timezone.utc),
        change_set={"version": 1, "precise": True, "changes": [{"series_code": "source-cases"}]} if precise else None,
        published_at=datetime.now(timezone.utc) if published else None)
    db.add(row)
    await db.commit()
    return row


async def test_scope_mismatch_is_rejected_before_a_candidate_can_be_published(selection_db):
    async with selection_db() as db:
        selected = await marker(db, "US")
        with pytest.raises(ValueError, match="country scopes do not match"):
            await data_selection.validate_data_selection(db, country_codes=["JP"], dirty_ids=[selected.id])


async def test_legacy_parent_requires_its_whole_available_family(selection_db):
    async with selection_db() as db:
        selected = await marker(db, "US", precise=False)
        plan = await data_selection.validate_data_selection(db,
            country_codes=["US", "US-AK"], dirty_ids=[selected.id])
        assert plan["country_codes"] == ["US", "US-AK"]
        with pytest.raises(ValueError, match="country scopes do not match"):
            await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id])


async def test_precise_parent_never_expands_and_returns_database_changes(selection_db):
    async with selection_db() as db:
        selected = await marker(db, "US")
        plan = await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id])
        assert plan["country_codes"] == ["US"]
        assert plan["available_country_codes"] == ["JP", "US", "US-AK"]
        assert plan["changes"] == [{"id": selected.id, "scope": "US", "change_set": selected.change_set}]
        with pytest.raises(ValueError, match="country scopes do not match"):
            await data_selection.validate_data_selection(db,
                country_codes=["US", "US-AK"], dirty_ids=[selected.id])


async def test_one_legacy_row_prevents_a_mixed_group_from_shrinking(selection_db):
    async with selection_db() as db:
        precise = await marker(db, "US")
        legacy = await marker(db, "US", precise=False)
        with pytest.raises(ValueError, match="country scopes do not match"):
            await data_selection.validate_data_selection(db,
                country_codes=["US"], dirty_ids=[precise.id, legacy.id])


@pytest.mark.parametrize("scope", ["US-CA", "CA", "UNKNOWN"])
async def test_inactive_nonpublic_and_unknown_scopes_cannot_disappear_from_selection(selection_db, scope):
    async with selection_db() as db:
        selected = await marker(db, scope)
        with pytest.raises(ValueError, match="disabled, unavailable or unknown"):
            await data_selection.validate_data_selection(db, country_codes=[scope], dirty_ids=[selected.id])


async def test_missing_dirty_id_and_wrong_line_are_rejected(selection_db):
    async with selection_db() as db:
        selected = await marker(db, "US")
        with pytest.raises(ValueError, match="missing"):
            await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id, 999])
        other_line = await marker(db, "*", line="literature")
        with pytest.raises(ValueError, match="surveillance change"):
            await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id, other_line.id])


async def test_ack_retry_uses_original_ids_and_does_not_absorb_new_changes(selection_db):
    async with selection_db() as db:
        selected = await marker(db, "US", published=True)
        later = await marker(db, "JP")
        plan = await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id])
        assert plan["dirty_ids"] == [selected.id]
        assert later.id not in plan["dirty_ids"]


@pytest.mark.parametrize("owner", ["same-task", "newer-task", None])
async def test_execution_can_only_retry_its_own_acknowledged_selection(selection_db, owner):
    async with selection_db() as db:
        selected = await marker(db, "US", published=True)
        selected.published_task_uuid = owner
        await db.commit()
        if owner == "same-task":
            plan = await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id], expected_task_uuid="same-task")
            assert plan["dirty_ids"] == [selected.id]
        else:
            with pytest.raises(ValueError, match="already published by another task"):
                await data_selection.validate_data_selection(db, country_codes=["US"], dirty_ids=[selected.id], expected_task_uuid="same-task")


@pytest.mark.parametrize("codes,ids", [
    (["US"], []), (["US"], [True]), (["US"], [0]), (["US"], [1, 1]),
    ([], [1]), (["us"], [1]), ([" US"], [1]), (["US", "US"], [1]),
])
async def test_malformed_selection_does_not_query_the_database(codes, ids):
    db = AsyncMock()
    with pytest.raises(ValueError):
        await data_selection.validate_data_selection(db, country_codes=codes, dirty_ids=ids)
    db.execute.assert_not_awaited()
