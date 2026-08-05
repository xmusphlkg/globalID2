from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

import pytest

from scripts import restore_disease_migration as restore


class _FakeDB:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement, parameters=None):
        self.executed.append((str(statement), parameters))
        return type("ExecuteResult", (), {"rowcount": 1})()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeDBContext:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> _FakeDB:
        return self.db

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


def _record(*, disease_id: int, deaths: int = 1) -> dict[str, Any]:
    return {
        "id": 101,
        "time": "2025-01-01T00:00:00+00:00",
        "disease_id": disease_id,
        "country_id": 9,
        "cases": 12,
        "deaths": deaths,
        "raw_data": {"Disease": "example", "Cases": "12"},
        "metadata": {"source": "registry", "revision": 1},
        "created_at": "2025-02-01T00:00:00+00:00",
        "updated_at": "2025-02-01T00:00:00+00:00",
    }


def _audit_row(
    *,
    operation: str,
    before: dict[str, Any],
    after: dict[str, Any] | None,
    new_disease_id: int | None = None,
    entity_table: str = "disease_records",
) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    if new_disease_id is not None:
        identity["new_disease_id"] = new_disease_id
    return {
        "id": 7,
        "migration_run_id": "run-1",
        "migration_key": "test-migration",
        "identity_key": "test-identity",
        "identity": identity,
        "entity_table": entity_table,
        "operation": operation,
        "before_data": before,
        "after_data": after,
    }


@pytest.mark.asyncio
async def test_restore_allows_deleted_legacy_projection_and_reinserts_before_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    before = _record(disease_id=193)
    audit = _audit_row(
        operation="legacy_projection_repair",
        before=before,
        after=None,
    )

    monkeypatch.setattr(restore, "get_db", lambda: _FakeDBContext(db))
    monkeypatch.setattr(restore, "_audit_table_exists", _async_value(True))
    monkeypatch.setattr(restore, "_acquire_restore_lock", _async_value(None))
    monkeypatch.setattr(restore, "_load_selected", _async_value([audit]))
    monkeypatch.setattr(restore, "_current_record", _async_value(None))

    result = await restore.restore_migration(
        migration_key="test-migration",
        run_id="run-1",
        apply=True,
    )

    assert result == {
        "mode": "apply",
        "status": "restored",
        "migration_key": "test-migration",
        "selected": 1,
        "restored": 1,
    }
    assert db.committed is True
    assert db.rolled_back is False
    assert len(db.executed) == 2
    assert "DELETE FROM disease_records" not in db.executed[0][0]
    assert "INSERT INTO disease_records" in db.executed[0][0]
    assert "UPDATE disease_migration_audit" in db.executed[1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("deaths", 2),
        ("metadata", {"source": "registry", "revision": 2}),
    ],
)
async def test_restore_rejects_later_changes_to_any_business_field(
    changed_field: str,
    changed_value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    before = _record(disease_id=24)
    after = _record(disease_id=105)
    current = deepcopy(after)
    current[changed_field] = changed_value
    audit = _audit_row(
        operation="fact_remap",
        before=before,
        after=after,
        new_disease_id=105,
    )
    current_records = iter([None, current])

    async def fake_current_record(*args, **kwargs):
        return next(current_records)

    monkeypatch.setattr(restore, "get_db", lambda: _FakeDBContext(db))
    monkeypatch.setattr(restore, "_audit_table_exists", _async_value(True))
    monkeypatch.setattr(restore, "_acquire_restore_lock", _async_value(None))
    monkeypatch.setattr(restore, "_load_selected", _async_value([audit]))
    monkeypatch.setattr(restore, "_current_record", fake_current_record)

    result = await restore.restore_migration(
        migration_key="test-migration",
        run_id="run-1",
        apply=False,
    )

    assert result["status"] == "blocked"
    assert result["ready"] == 0
    assert result["blocked"] == [
        {
            "audit_id": 7,
            "identity_key": "test-identity",
            "operation": "fact_remap",
            "entity_table": "disease_records",
            "errors": ["post-migration observation content has changed"],
        }
    ]
    assert db.rolled_back is True
    assert db.committed is False
    assert db.executed == []


def test_complete_after_image_comparison_fails_closed() -> None:
    record = _record(disease_id=105)

    assert restore._same_observation_content(record, deepcopy(record)) is True
    assert restore._same_observation_content(record, "not-json") is False
    assert restore._same_observation_content(None, None) is False


@pytest.mark.asyncio
async def test_restore_apply_requires_one_exact_run_id() -> None:
    with pytest.raises(ValueError, match="exact migration run_id"):
        await restore.restore_migration(
            migration_key="test-migration",
            apply=True,
        )


def test_audit_timestamp_is_decoded_for_asyncpg() -> None:
    value = restore._timestamp_value("2025-01-01T00:00:00Z")

    assert isinstance(value, datetime)
    assert value.isoformat() == "2025-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="Invalid audit timestamp"):
        restore._timestamp_value("not-a-timestamp")


@pytest.mark.asyncio
async def test_current_record_can_lock_the_compared_row_for_update() -> None:
    class ScalarResult:
        @staticmethod
        def scalar():
            return {"disease_id": 105}

    class LockingDB:
        statement = ""

        async def execute(self, statement, parameters=None):
            self.statement = str(statement)
            return ScalarResult()

    db = LockingDB()
    result = await restore._current_record(
        db,
        time_value="2025-01-01T00:00:00+00:00",
        disease_id=105,
        country_id=9,
        for_update=True,
    )

    assert result == {"disease_id": 105}
    assert "FOR UPDATE" in db.statement


@pytest.mark.asyncio
async def test_restore_advisory_lock_serializes_overlapping_previews() -> None:
    db = _FakeDB()

    await restore._acquire_restore_lock(db)

    assert len(db.executed) == 1
    statement, parameters = db.executed[0]
    assert "pg_advisory_xact_lock" in statement
    assert parameters == {"lock_key": restore.RESTORE_ADVISORY_LOCK_KEY}


@pytest.mark.asyncio
async def test_restore_series_geography_uses_composite_key_and_before_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    before = {
        "time": "2025-01-04T00:00:00+00:00",
        "series_code": "SER_US_EXAMPLE",
        "geography_key": "country:US:national",
        "dimension_key": "all",
        "dimensions": {},
        "value": 12.0,
        "unit": "count",
        "suppressed": False,
        "suppression_reason": None,
        "quality_status": "provisional",
        "raw_data": {"ReportingArea": "TOTAL"},
        "metadata": {},
        "created_at": "2025-02-01T00:00:00+00:00",
        "updated_at": "2025-02-01T00:00:00+00:00",
    }
    after = deepcopy(before)
    after["geography_key"] = "source:SRC_US_NNDSS:reporting-area:total"
    audit = _audit_row(
        operation="series_geography_remap",
        entity_table="disease_series_observations",
        before=before,
        after=after,
    )
    audit["identity"] = {
        "new_geography_key": after["geography_key"],
    }
    current_series = iter([None, after])

    async def fake_current_series(*args, **kwargs):
        return next(current_series)

    monkeypatch.setattr(restore, "get_db", lambda: _FakeDBContext(db))
    monkeypatch.setattr(restore, "_audit_table_exists", _async_value(True))
    monkeypatch.setattr(restore, "_acquire_restore_lock", _async_value(None))
    monkeypatch.setattr(restore, "_load_selected", _async_value([audit]))
    monkeypatch.setattr(restore, "_current_series_observation", fake_current_series)

    result = await restore.restore_migration(
        migration_key="test-migration", run_id="run-1", apply=True
    )

    assert result["status"] == "restored"
    assert result["restored"] == 1
    assert "DELETE FROM disease_series_observations" in db.executed[0][0]
    assert "INSERT INTO disease_series_observations" in db.executed[1][0]
    assert db.executed[0][1]["geography_key"] == after["geography_key"]
    assert db.committed is True


@pytest.mark.asyncio
async def test_restore_resident_backfill_deletes_unchanged_insert_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    after = {
        "time": "2025-01-04T00:00:00+00:00",
        "series_code": "SER_US_EXAMPLE",
        "geography_key": "country:US:national",
        "dimension_key": "all",
        "dimensions": {},
        "value": 11.0,
        "unit": "count",
        "suppressed": False,
        "suppression_reason": None,
        "quality_status": "provisional",
        "raw_data": {"ReportingArea": "U.S. Residents"},
        "metadata": {"population_scope": "us_residents_excluding_territories"},
        "created_at": "2025-02-01T00:00:00+00:00",
        "updated_at": "2025-02-01T00:00:00+00:00",
    }
    audit = _audit_row(
        operation="series_resident_backfill_insert",
        entity_table="disease_series_observations",
        before={},
        after=after,
    )
    audit["identity"] = {
        key: after[key]
        for key in ("time", "series_code", "geography_key", "dimension_key")
    }

    monkeypatch.setattr(restore, "get_db", lambda: _FakeDBContext(db))
    monkeypatch.setattr(restore, "_audit_table_exists", _async_value(True))
    monkeypatch.setattr(restore, "_acquire_restore_lock", _async_value(None))
    monkeypatch.setattr(restore, "_load_selected", _async_value([audit]))
    monkeypatch.setattr(restore, "_current_series_observation", _async_value(after))

    result = await restore.restore_migration(
        migration_key="test-migration", run_id="run-1", apply=True
    )

    assert result["status"] == "restored"
    sql = [statement for statement, _ in db.executed]
    assert sum("DELETE FROM disease_series_observations" in item for item in sql) == 1
    assert not any("INSERT INTO disease_series_observations" in item for item in sql)
    assert "restored_at IS NULL" in sql[-1]


@pytest.mark.asyncio
async def test_restore_resident_backfill_requires_empty_before_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDB()
    after = {
        "time": "2025-01-04T00:00:00+00:00",
        "series_code": "SER_US_EXAMPLE",
        "geography_key": "country:US:national",
        "dimension_key": "all",
        "value": 11.0,
    }
    audit = _audit_row(
        operation="series_resident_backfill_insert",
        entity_table="disease_series_observations",
        before={"unexpected": "not-an-insert"},
        after=after,
    )
    audit["identity"] = {
        key: after[key]
        for key in ("time", "series_code", "geography_key", "dimension_key")
    }

    monkeypatch.setattr(restore, "get_db", lambda: _FakeDBContext(db))
    monkeypatch.setattr(restore, "_audit_table_exists", _async_value(True))
    monkeypatch.setattr(restore, "_acquire_restore_lock", _async_value(None))
    monkeypatch.setattr(restore, "_load_selected", _async_value([audit]))
    monkeypatch.setattr(
        restore, "_current_series_observation", _async_value(after)
    )

    result = await restore.restore_migration(
        migration_key="test-migration", run_id="run-1", apply=False
    )

    assert result["status"] == "blocked"
    assert result["blocked"][0]["errors"] == [
        "resident backfill insert audit before-image is not empty"
    ]


@pytest.mark.asyncio
async def test_current_series_observation_can_lock_composite_key() -> None:
    class ScalarResult:
        @staticmethod
        def scalar():
            return {"series_code": "SER_US_EXAMPLE"}

    class LockingDB:
        statement = ""
        parameters = None

        async def execute(self, statement, parameters=None):
            self.statement = str(statement)
            self.parameters = parameters
            return ScalarResult()

    db = LockingDB()
    result = await restore._current_series_observation(
        db,
        time_value="2025-01-04T00:00:00+00:00",
        series_code="SER_US_EXAMPLE",
        geography_key="source:SRC_US_NNDSS:reporting-area:total",
        dimension_key="all",
        for_update=True,
    )

    assert result == {"series_code": "SER_US_EXAMPLE"}
    assert "FOR UPDATE" in db.statement
    assert db.parameters["dimension_key"] == "all"


def _async_value(value):
    async def result(*args, **kwargs):
        return value

    return result
