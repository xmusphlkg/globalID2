"""A literature candidate can only acknowledge its own frozen change IDs."""

from datetime import datetime, timezone

import pytest

from src.domain.collection import PublishDirty
from src.services.data_release.global_selection import acknowledge_global_selection, validate_global_selection
from tests.unit.test_release_dirty_selection import release_db


async def test_literature_selection_fences_other_channels_and_retries(release_db):
    maker, _, _ = release_db
    now = datetime.now(timezone.utc)
    async with maker() as db:
        rows = [PublishDirty(line=line, scope_key=scope, source_task_uuid="source", marked_at=now)
                for line, scope in (("literature", "*"), ("literature", "*"),
                                    ("situation", "*"), ("literature", "invalid"))]
        db.add_all(rows)
        await db.commit()
        selected, later, other, invalid = [row.id for row in rows]
        await validate_global_selection(db, [selected], line="literature", task_uuid="release")
        for ids in ([selected, other], [invalid], [999999], [selected, selected], [True]):
            with pytest.raises(ValueError):
                await validate_global_selection(db, ids, line="literature", task_uuid="release")
        rows[0].published_at, rows[0].published_task_uuid = now, "release"
        await db.commit()
        await validate_global_selection(db, [selected], line="literature", task_uuid="release")
        with pytest.raises(ValueError, match="already consumed"):
            await validate_global_selection(db, [selected], line="literature", task_uuid="other")
        assert (await db.get(PublishDirty, later)).published_at is None


async def test_unknown_global_channel_is_rejected(release_db):
    maker, _, _ = release_db
    async with maker() as db:
        with pytest.raises(ValueError, match="Unknown global"):
            await validate_global_selection(db, [1], line="surveillance")


async def test_acknowledgement_only_consumes_frozen_ids_and_is_idempotent(release_db):
    maker, _, _ = release_db
    async with maker() as db:
        rows = [PublishDirty(line="literature", scope_key="*", source_task_uuid="source",
                             marked_at=datetime.now(timezone.utc)) for _ in range(2)]
        db.add_all(rows)
        await db.commit()
        await acknowledge_global_selection(db, [rows[0].id], line="literature", task_uuid="one")
        await db.commit()
        await acknowledge_global_selection(db, [rows[0].id], line="literature", task_uuid="one")
        await db.commit()
        assert rows[0].published_task_uuid == "one" and rows[0].published_at is not None
        assert rows[1].published_at is None
        with pytest.raises(ValueError, match="already consumed"):
            await acknowledge_global_selection(db, [rows[0].id], line="literature", task_uuid="two")


async def test_large_selection_uses_bounded_transaction_batches(release_db, monkeypatch):
    from src.services.data_release import global_selection
    maker, _, statements = release_db
    monkeypatch.setattr(global_selection, 'SELECTION_BATCH_SIZE', 2)
    async with maker() as db:
        rows = [PublishDirty(line='literature', scope_key='*', source_task_uuid='source',
                             marked_at=datetime.now(timezone.utc)) for _ in range(5)]
        db.add_all(rows)
        await db.commit()
        statements.clear()
        await acknowledge_global_selection(db, [row.id for row in rows], line='literature', task_uuid='batch')
        await db.commit()
        assert sum(statement.lstrip().upper().startswith('SELECT') for statement in statements) == 3
        assert sum(statement.lstrip().upper().startswith('UPDATE') for statement in statements) == 3
        assert all(row.published_task_uuid == 'batch' for row in rows)
