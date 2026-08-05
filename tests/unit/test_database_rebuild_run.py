from __future__ import annotations

import json

import pytest

from scripts.full_rebuild_database import DatabaseRebuilder
from src.services.database_rebuild_import import insert_with_savepoint_fallback
from src.services.database_rebuild_run import (
    RebuildIncompleteError,
    RebuildRunTracker,
    RebuildStage,
    execute_rebuild_stages,
)


class FakeDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class FailingCompletionTracker(RebuildRunTracker):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.persist_calls = 0

    def _persist(self) -> None:
        self.persist_calls += 1
        if self.persist_calls == 3:
            raise OSError("checkpoint disk unavailable")
        super()._persist()


@pytest.mark.asyncio
async def test_middle_stage_failure_persists_partial_completion(tmp_path) -> None:
    calls = []

    async def completed_stage() -> None:
        calls.append("clear_data")

    async def failed_stage() -> None:
        calls.append("import_standard")
        tracker.record_partial_commit("import_standard", {"rows": 1000})
        raise ValueError("bad standard disease row")

    async def unreachable_stage() -> None:
        calls.append("verify")

    checkpoint = tmp_path / "rebuild.json"
    tracker = RebuildRunTracker(
        checkpoint,
        country_code="cn",
        mode="full",
        stage_names=("clear_data", "import_standard", "verify"),
    )
    db = FakeDb()

    with pytest.raises(RebuildIncompleteError) as captured:
        await execute_rebuild_stages(
            db,
            (
                RebuildStage("clear_data", "Clear", completed_stage),
                RebuildStage("import_standard", "Import", failed_stage),
                RebuildStage("verify", "Verify", unreachable_stage, commits_changes=False),
            ),
            tracker,
        )

    persisted = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert calls == ["clear_data", "import_standard"]
    assert db.rollbacks == 1
    assert persisted["status"] == "failed"
    assert persisted["completed_stages"] == ["clear_data"]
    assert persisted["committed_stages"] == ["clear_data"]
    assert persisted["failed_stage"] == "import_standard"
    assert persisted["partial_stage_commits"] == {
        "import_standard": {"rows": 1000}
    }
    assert persisted["error"] == {
        "type": "ValueError",
        "message": "bad standard disease row",
    }
    assert captured.value.snapshot == persisted
    assert "Already committed stages: ['clear_data']" in str(captured.value)


@pytest.mark.asyncio
async def test_success_report_does_not_claim_read_only_verification_committed(tmp_path) -> None:
    async def no_op() -> None:
        return None

    checkpoint = tmp_path / "rebuild.json"
    tracker = RebuildRunTracker(
        checkpoint,
        country_code="us",
        mode="history",
        stage_names=("import_history", "verify"),
    )

    report = await execute_rebuild_stages(
        FakeDb(),
        (
            RebuildStage("import_history", "Import", no_op),
            RebuildStage("verify", "Verify", no_op, commits_changes=False),
        ),
        tracker,
    )

    assert report["status"] == "completed"
    assert report["completed_stages"] == ["import_history", "verify"]
    assert report["committed_stages"] == ["import_history"]
    assert report["failed_stage"] is None


@pytest.mark.asyncio
async def test_stage_completion_checkpoint_failure_is_incomplete(tmp_path) -> None:
    async def committed_action() -> None:
        return None

    checkpoint = tmp_path / "rebuild.json"
    tracker = FailingCompletionTracker(
        checkpoint,
        country_code="cn",
        mode="full",
        stage_names=("clear_data",),
    )
    db = FakeDb()

    with pytest.raises(RebuildIncompleteError) as captured:
        await execute_rebuild_stages(
            db,
            (RebuildStage("clear_data", "Clear", committed_action),),
            tracker,
        )

    persisted = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert db.rollbacks == 1
    assert persisted["status"] == "failed"
    assert persisted["failed_stage"] == "clear_data"
    assert persisted["committed_stages"] == ["clear_data"]
    assert persisted["error"] == {
        "type": "OSError",
        "message": "checkpoint disk unavailable",
    }
    assert isinstance(captured.value.__cause__, OSError)


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class SavepointDb:
    def __init__(self) -> None:
        self.inserted = []
        self.rollback_called = False

    def begin_nested(self):
        return _NestedTransaction()

    async def execute(self, _statement, rows):
        if isinstance(rows, list):
            raise ValueError("batch rejected")
        if rows["id"] == "bad":
            raise ValueError("row rejected")
        self.inserted.append(rows["id"])

    async def rollback(self) -> None:
        self.rollback_called = True


@pytest.mark.asyncio
async def test_batch_fallback_uses_savepoints_without_outer_rollback() -> None:
    db = SavepointDb()

    result = await insert_with_savepoint_fallback(
        db,
        object(),
        [{"id": "first"}, {"id": "bad"}, {"id": "last"}],
    )

    assert result.attempted == 3
    assert result.inserted == 2
    assert result.failed == 1
    assert result.batch_error == "batch rejected"
    assert db.inserted == ["first", "last"]
    assert db.rollback_called is False


@pytest.mark.asyncio
async def test_rebuilder_refuses_to_commit_incomplete_batch() -> None:
    db = SavepointDb()

    with pytest.raises(RuntimeError, match="1/1 rows failed"):
        await DatabaseRebuilder._batch_insert(
            object.__new__(DatabaseRebuilder),
            db,
            [{"id": "bad"}],
        )

    assert db.rollback_called is False


class CommitDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FailingProgressTracker:
    def record_partial_commit(self, _stage_name, _details) -> None:
        raise OSError("cannot persist history checkpoint")


@pytest.mark.asyncio
async def test_history_progress_checkpoint_failure_is_not_a_skipped_row() -> None:
    rebuilder = object.__new__(DatabaseRebuilder)
    rebuilder._run_tracker = FailingProgressTracker()
    db = CommitDb()

    with pytest.raises(OSError, match="cannot persist history checkpoint"):
        await rebuilder._commit_history_progress(
            db,
            rows_processed=1000,
            total_rows=2000,
            inserted=1000,
            skipped=0,
        )

    assert db.commits == 1
