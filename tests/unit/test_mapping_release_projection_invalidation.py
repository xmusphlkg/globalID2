"""Mapping activation and coarse projection invalidation share one transaction."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.domain import (
    Base,
    DiseaseMappingAssertion,
    DiseaseMappingRelease,
    DiseaseMappingReleaseItem,
    SourceDiseaseCategory,
)
from src.domain.collection import PublishDirty
from src.services.data_release.planning import plan_changes
from src.services.disease_mapping_registry_service import DiseaseMappingRegistryService


@pytest.fixture
async def mapping_database():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            SourceDiseaseCategory.__table__, DiseaseMappingAssertion.__table__,
            DiseaseMappingRelease.__table__, DiseaseMappingReleaseItem.__table__,
            PublishDirty.__table__,
        ]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    service = DiseaseMappingRegistryService()
    # Production readiness runs separately; activation must not provision or
    # repair the publication schema as a side effect of changing mappings.
    service._schema_ready = True
    try:
        yield maker, service
    finally:
        await engine.dispose()


async def _release(db, name, countries, *, status):
    release = DiseaseMappingRelease(
        release_code=name, status=status, checksum=name.ljust(64, "0"),
        created_by="offline-fixture", metadata_={},
    )
    db.add(release)
    await db.flush()
    now = datetime.now(timezone.utc)
    for index, code in enumerate(countries):
        category = SourceDiseaseCategory(
            category_key=f"{name}-{index}", country_code=code,
            source_id=f"source-{name}", source_code=str(index),
            canonical_source_label=f"Disease {index}", normalized_label=f"disease {index}",
            first_seen_at=now, last_seen_at=now,
        )
        db.add(category)
        await db.flush()
        assertion = DiseaseMappingAssertion(
            assertion_key=f"assertion-{name}-{index}", category_id=category.id,
            target_kind="concept", target_code=f"D{index:03d}",
            mapping_relation="exact", comparability="direct",
            projection_policy="canonical", aggregation_policy="direct_only",
            assertion_status="approved", suggestion_method="offline-fixture",
        )
        db.add(assertion)
        await db.flush()
        db.add(DiseaseMappingReleaseItem(release_id=release.id, assertion_id=assertion.id))
    await db.flush()
    return release


@pytest.mark.parametrize("rollback", [False, True])
async def test_activation_and_old_new_parent_markers_are_atomic(mapping_database, rollback):
    maker, service = mapping_database
    async with maker() as db:
        old = await _release(db, "old", ["DE", "JP-25"], status="active")
        new = await _release(db, "new", ["US", "JP-26", "US"], status="draft")
        old_id, new_id = old.id, new.id
        # Superseded/withdrawn targets still invalidate the old jurisdiction.
        old_assertions = (await db.execute(select(DiseaseMappingAssertion).join(
            DiseaseMappingReleaseItem, DiseaseMappingReleaseItem.assertion_id == DiseaseMappingAssertion.id,
        ).where(DiseaseMappingReleaseItem.release_id == old_id))).scalars().all()
        for assertion in old_assertions:
            assertion.assertion_status = "superseded"
            assertion.projection_policy = "no_projection"
        await db.commit()
    async with maker() as db:
        await service.activate_release(db, new_id)
        await (db.rollback() if rollback else db.commit())
    async with maker() as db:
        statuses = dict((await db.execute(select(DiseaseMappingRelease.id, DiseaseMappingRelease.status))).all())
        dirty = (await db.execute(select(PublishDirty).order_by(PublishDirty.scope_key))).scalars().all()
        if rollback:
            assert statuses == {old_id: "active", new_id: "draft"}
            assert dirty == []
        else:
            assert statuses == {old_id: "superseded", new_id: "active"}
            assert [row.scope_key for row in dirty] == ["DE", "JP", "US"]
            assert all(row.line == "surveillance" and row.change_set is None and row.published_at is None for row in dirty)
            assert len({row.source_task_uuid for row in dirty}) == 1
            assert UUID(dirty[0].source_task_uuid).version == 5
            plan = plan_changes(dirty, {"DE", "JP", "JP-25", "JP-26", "US", "US-CA"}, full=True)
            assert plan["country_codes"] == ["DE", "JP", "JP-25", "JP-26", "US", "US-CA"]


async def test_empty_replacement_invalidates_countries_only_in_old_release(mapping_database):
    maker, service = mapping_database
    async with maker() as db:
        await _release(db, "old-only", ["SE", "DE"], status="active")
        new = await _release(db, "withdraw-all", [], status="draft")
        await db.commit()
        await service.activate_release(db, new.id)
        await db.commit()
    async with maker() as db:
        assert (await db.execute(select(PublishDirty.scope_key).order_by(PublishDirty.scope_key))).scalars().all() == ["DE", "SE"]


@pytest.mark.parametrize("existing_empty", [False, True])
async def test_empty_initial_or_empty_to_empty_release_has_no_phantom_scopes(mapping_database, existing_empty):
    maker, service = mapping_database
    async with maker() as db:
        if existing_empty:
            await _release(db, "empty-old", [], status="active")
        new = await _release(db, "empty-new", [], status="draft")
        await db.commit()
        await service.activate_release(db, new.id)
        await db.commit()
    async with maker() as db:
        assert (await db.execute(select(PublishDirty))).scalars().all() == []
        assert (await db.get(DiseaseMappingRelease, new.id)).status == "active"


async def test_repeated_activation_is_rejected_without_duplicate_markers(mapping_database):
    maker, service = mapping_database
    async with maker() as db:
        new = await _release(db, "repeat", ["NZ", "NZ"], status="draft")
        await db.commit()
        await service.activate_release(db, new.id)
        await db.commit()
        with pytest.raises(ValueError, match="draft mapping release"):
            await service.activate_release(db, new.id)
        await db.rollback()
    async with maker() as db:
        assert (await db.execute(select(PublishDirty.scope_key))).scalars().all() == ["NZ"]


async def test_missing_publication_schema_fails_and_activation_rolls_back(mapping_database):
    maker, service = mapping_database
    async with maker() as db:
        old = await _release(db, "before-missing", ["JP"], status="active")
        new = await _release(db, "after-missing", ["US"], status="draft")
        old_id, new_id = old.id, new.id
        await db.execute(text("DROP TABLE publish_dirty"))
        await db.commit()
        with pytest.raises(OperationalError, match="publish_dirty"):
            await service.activate_release(db, new_id)
        await db.rollback()
    async with maker() as db:
        assert dict((await db.execute(select(DiseaseMappingRelease.id, DiseaseMappingRelease.status))).all()) == {old_id: "active", new_id: "draft"}
        assert (await db.execute(text("SELECT name FROM sqlite_master WHERE name = 'publish_dirty'"))).scalar_one_or_none() is None
