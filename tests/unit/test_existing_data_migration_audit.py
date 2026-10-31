"""The initial migration preserves existing publication and marks only evidence of change."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from scripts import audit_existing_data_migration as audit
from src.domain import (
    Base,
    Country,
    CountryPublishState,
    PublishDirty,
    RunStage,
    Task,
    TaskType,
)

NOW = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)


@pytest.fixture
def snapshot():
    return {
        "countries": [{"country_code": "US", "is_active": True}],
        "observations": [
            {
                "country_code": "US",
                "row_count": 3,
                "source_high_water": NOW.replace(tzinfo=None),
            }
        ],
        "legacy": [],
        "public": [
            {
                "country_code": "US",
                "disease_id": "D001",
                "content_hash": "a" * 64,
                "projection_version": audit.PROJECTION_VERSION,
                "source_high_water": NOW,
                "point_count": 3,
                "updated_at": NOW,
            }
        ],
        "states": [
            {
                "country_code": "US",
                "content_hash": audit.country_publish_hash(["a" * 64]),
                "series_count": 1,
                "point_count": 3,
                "projection_version": audit.PROJECTION_VERSION,
                "source_high_water": NOW,
                "published_at": NOW,
            }
        ],
        "pending": [],
    }


def scope(snapshot):
    return audit.analyze(snapshot)["scopes"][0]


def test_matching_publication_is_preserved_with_naive_utc_source(snapshot):
    row = scope(snapshot)
    assert row["state_matches_public_metadata"] and row["watermark_matches"]
    assert not row["mark_dirty"] and row["seed_state"] is None
    assert row["dirty_reasons"] == []
    assert "population_mapping_policy_fingerprints_unavailable" in row["uncertain"]
    assert (
        "watermark_cannot_detect_all_deletions_or_backdated_edits" in row["uncertain"]
    )


@pytest.mark.parametrize("days", [-1, 1])
def test_any_watermark_change_is_dirty_including_removal_of_latest_rows(snapshot, days):
    snapshot["observations"][0]["source_high_water"] = NOW + timedelta(days=days)
    assert scope(snapshot)["dirty_reasons"] == ["observation_watermark_changed"]
    assert scope(snapshot)["mark_dirty"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_hash", "b" * 64),
        ("series_count", 2),
        ("point_count", 4),
        ("projection_version", "old"),
    ],
)
def test_public_state_metadata_inconsistency_is_dirty(snapshot, field, value):
    snapshot["states"][0][field] = value
    assert "public_state_metadata_mismatch" in scope(snapshot)["dirty_reasons"]
    assert scope(snapshot)["mark_dirty"]


def test_missing_state_is_seeded_from_existing_public_metadata_without_reprojection(
    snapshot,
):
    snapshot["states"] = []
    row = scope(snapshot)
    assert not row["mark_dirty"]
    assert row["seed_state"]["published_at"] == NOW
    assert row["seed_state"]["content_hash"] == audit.country_publish_hash(["a" * 64])


def test_mixed_public_watermarks_are_not_invented_into_a_published_state(snapshot):
    snapshot["states"] = []
    other = {
        **snapshot["public"][0],
        "disease_id": "D002",
        "source_high_water": NOW - timedelta(days=1),
    }
    snapshot["public"].append(other)
    row = scope(snapshot)
    assert row["seed_state"] is None and row["mark_dirty"]
    assert "inconsistent_public_snapshot_without_state" in row["dirty_reasons"]


def test_unpublished_source_is_detected_without_historical_task_scan(snapshot):
    snapshot["states"], snapshot["public"] = [], []
    row = scope(snapshot)
    assert row["dirty_reasons"] == ["source_has_no_publication_snapshot"]
    assert row["mark_dirty"]
    assert audit.analyze(snapshot)["summary"]["public_scopes"] == 0


def test_existing_pending_scope_is_not_duplicated(snapshot):
    snapshot["observations"][0]["source_high_water"] = NOW + timedelta(days=1)
    snapshot["pending"] = [{"country_code": "US"}]
    row = scope(snapshot)
    assert row["dirty_reasons"] and row["already_pending"]
    assert not row["mark_dirty"]


def test_private_country_remains_unpublished(snapshot, monkeypatch):
    snapshot["states"], snapshot["public"] = [], []
    monkeypatch.setattr(
        audit,
        "get_country_bootstrap_config",
        lambda _: {"public_release_enabled": False},
    )
    row = scope(snapshot)
    assert row["dirty_reasons"] and not row["public_release_enabled"]
    assert not row["mark_dirty"] and row["seed_state"] is None


def test_readonly_guard_precedes_evidence_and_always_rolls_back(snapshot, monkeypatch):
    calls = []

    class Connection:
        dialect = SimpleNamespace(name="postgresql")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, statement):
            calls.append(str(statement))

        def rollback(self):
            calls.append("rollback")

        def commit(self):
            pytest.fail("read-only audit committed")

    def load(connection):
        assert calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        return snapshot

    monkeypatch.setattr(audit, "load_snapshot", load)
    result = audit.run(SimpleNamespace(connect=lambda: Connection()))
    assert result["mode"] == "read_only" and calls[-1] == "rollback"
    assert result["summary"]["new_dirty_scopes"] == 0


@pytest.fixture
def database():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def sqlite_functions(connection, _record):
        connection.create_function("char_length", 1, len)

    Base.metadata.create_all(
        engine,
        tables=[
            Country.__table__,
            Task.__table__,
            PublishDirty.__table__,
            RunStage.__table__,
            CountryPublishState.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all(
            [
                Country(code="US", name="US", name_en="US"),
                Country(code="JP", name="JP", name_en="JP"),
            ]
        )
        session.commit()
    yield engine
    engine.dispose()


def test_apply_creates_real_manual_store_task_and_is_idempotent(snapshot, database):
    snapshot["observations"][0]["source_high_water"] = NOW + timedelta(days=1)
    report = audit.analyze(snapshot)
    with Session(database) as session:
        session.add(
            PublishDirty(
                line="surveillance",
                scope_key="JP",
                source_task_uuid="existing",
                marked_at=NOW,
            )
        )
        session.commit()
        result = audit.apply_actions(session, report)
        session.commit()
        assert result["dirty_inserted"] == 1 and result["task_uuid"]
        assert audit.apply_actions(session, report)["task_uuid"] is None
        tasks = session.scalars(select(Task)).all()
        assert len(tasks) == 1 and tasks[0].task_type == TaskType.PROCESS_DATA
        stages = session.scalars(select(RunStage)).all()
        assert len(stages) == 1 and stages[0].stage == "store"
        assert stages[0].metrics["manual_store"] is True
        markers = session.scalars(select(PublishDirty).order_by(PublishDirty.id)).all()
        assert [(row.scope_key, row.source_task_uuid) for row in markers] == [
            ("JP", "existing"),
            ("US", result["task_uuid"]),
        ]
        assert all(row.published_at is None for row in markers)


def test_apply_missing_state_keeps_original_published_timestamp(snapshot, database):
    snapshot["states"] = []
    report = audit.analyze(snapshot)
    with Session(database) as session:
        result = audit.apply_actions(session, report)
        session.commit()
        state = session.get(CountryPublishState, "US")
        assert audit.utc(state.published_at) == NOW
        assert state.content_hash == report["scopes"][0]["public"]["content_hash"]
        assert result["states_seeded"] == 1 and result["dirty_inserted"] == 0
        assert audit.apply_actions(session, report)["states_seeded"] == 0


def test_apply_rolls_back_task_stage_and_marker_together(snapshot, database):
    snapshot["public"], snapshot["states"] = [], []
    report = audit.analyze(snapshot)
    with Session(database) as session:
        audit.apply_actions(session, report)
        session.rollback()
        assert session.scalars(select(Task)).all() == []
        assert session.scalars(select(RunStage)).all() == []
        assert session.scalars(select(PublishDirty)).all() == []


def test_no_changes_does_not_create_a_migration_task(snapshot, database):
    with Session(database) as session:
        assert (
            audit.apply_actions(session, audit.analyze(deepcopy(snapshot)))["task_uuid"]
            is None
        )
        assert session.scalars(select(Task)).all() == []


def test_apply_session_preserves_outer_transaction_ownership(snapshot, database):
    snapshot["public"], snapshot["states"] = [], []
    with database.connect() as connection, connection.begin():
        with Session(bind=connection) as session:
            audit.apply_actions(session, audit.analyze(snapshot))
            session.commit()
        assert connection.in_transaction()
    with Session(database) as session:
        assert len(session.scalars(select(Task)).all()) == 1
        assert len(session.scalars(select(PublishDirty)).all()) == 1
