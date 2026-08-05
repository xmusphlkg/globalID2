"""Stage execution and durable status reporting for database rebuilds.

Database rebuild stages intentionally commit independently.  Historical imports
can be large and use savepoints to recover from bad rows, so treating the whole
rebuild as one transaction is neither reliable nor operationally practical.
This module makes that contract explicit: every committed stage is recorded in
an atomically replaced JSON checkpoint, and a failed run reports which stages
may already be visible in the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Sequence


StageAction = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class RebuildStage:
    """One independently committed rebuild stage."""

    name: str
    label: str
    action: StageAction
    commits_changes: bool = True


class RebuildIncompleteError(RuntimeError):
    """Raised when a rebuild stops after zero or more committed stages."""

    def __init__(self, snapshot: dict[str, object]):
        self.snapshot = snapshot
        committed = snapshot.get("committed_stages", [])
        partial = snapshot.get("partial_stage_commits", {})
        failed = snapshot.get("failed_stage") or "unknown"
        checkpoint = snapshot.get("checkpoint_file")
        super().__init__(
            f"Database rebuild failed during stage {failed!r}. "
            f"Already committed stages: {committed or 'none'}; "
            f"partial stage commits: {partial or 'none'}. "
            f"Inspect checkpoint {checkpoint!s} and rerun the same mode; "
            "rebuild stages are designed to be safely repeatable."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RebuildRunTracker:
    """Persist the latest rebuild state using atomic file replacement."""

    def __init__(
        self,
        checkpoint_file: Path,
        *,
        country_code: str,
        mode: str,
        stage_names: Iterable[str],
    ) -> None:
        self.checkpoint_file = checkpoint_file
        self._snapshot: dict[str, object] = {
            "schema_version": 1,
            "country_code": country_code.upper(),
            "mode": mode,
            "status": "pending",
            "planned_stages": list(stage_names),
            "completed_stages": [],
            "committed_stages": [],
            "partial_stage_commits": {},
            "active_stage": None,
            "failed_stage": None,
            "error": None,
            "rollback_error": None,
            "started_at": None,
            "updated_at": None,
            "completed_at": None,
            "checkpoint_file": str(checkpoint_file),
        }

    @property
    def snapshot(self) -> dict[str, object]:
        """Return a serialization-safe copy of the current state."""
        return json.loads(json.dumps(self._snapshot))

    def start(self) -> None:
        now = _utc_now()
        self._snapshot.update(status="running", started_at=now, updated_at=now)
        self._persist()

    def begin_stage(self, stage_name: str) -> None:
        self._snapshot.update(active_stage=stage_name, updated_at=_utc_now())
        self._persist()

    def complete_stage(self, stage_name: str, *, committed: bool) -> None:
        completed = self._snapshot["completed_stages"]
        assert isinstance(completed, list)
        if stage_name not in completed:
            completed.append(stage_name)
        if committed:
            committed_stages = self._snapshot["committed_stages"]
            assert isinstance(committed_stages, list)
            if stage_name not in committed_stages:
                committed_stages.append(stage_name)
        partial = self._snapshot["partial_stage_commits"]
        assert isinstance(partial, dict)
        partial.pop(stage_name, None)
        self._snapshot.update(active_stage=None, updated_at=_utc_now())
        self._persist()

    def record_partial_commit(self, stage_name: str, details: dict[str, object]) -> None:
        """Record a durable chunk commit made before a stage fully completes."""
        partial = self._snapshot["partial_stage_commits"]
        assert isinstance(partial, dict)
        partial[stage_name] = details
        self._snapshot.update(updated_at=_utc_now())
        self._persist()

    def fail(
        self,
        stage_name: str,
        error: BaseException,
        *,
        rollback_error: BaseException | None = None,
    ) -> None:
        now = _utc_now()
        self._snapshot.update(
            status="failed",
            active_stage=None,
            failed_stage=stage_name,
            error={"type": type(error).__name__, "message": str(error)[:2000]},
            rollback_error=(
                {"type": type(rollback_error).__name__, "message": str(rollback_error)[:2000]}
                if rollback_error is not None
                else None
            ),
            updated_at=now,
            completed_at=now,
        )
        self._persist()

    def finish(self) -> None:
        now = _utc_now()
        self._snapshot.update(
            status="completed",
            active_stage=None,
            updated_at=now,
            completed_at=now,
        )
        self._persist()

    def _persist(self) -> None:
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint_file.with_suffix(
            self.checkpoint_file.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(self._snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.checkpoint_file)


async def execute_rebuild_stages(
    db,
    stages: Sequence[RebuildStage],
    tracker: RebuildRunTracker,
    *,
    on_stage_start: Callable[[int, int, RebuildStage], None] | None = None,
) -> dict[str, object]:
    """Run stages and turn any partial completion into an explicit failure."""
    try:
        tracker.start()
    except Exception as error:
        snapshot = tracker.snapshot
        snapshot.update(
            status="failed",
            failed_stage="checkpoint_initialization",
            checkpoint_persistence_error={
                "type": type(error).__name__,
                "message": str(error)[:2000],
            },
        )
        raise RebuildIncompleteError(snapshot) from error

    total = len(stages)
    for index, stage in enumerate(stages, start=1):
        if on_stage_start is not None:
            on_stage_start(index, total, stage)
        try:
            tracker.begin_stage(stage.name)
            await stage.action()
            # A stage action may already have committed.  Persisting its
            # completion is therefore part of the same failure boundary.
            tracker.complete_stage(stage.name, committed=stage.commits_changes)
        except Exception as error:
            rollback_error = None
            try:
                await db.rollback()
            except Exception as rollback_failure:
                rollback_error = rollback_failure
            checkpoint_error = None
            try:
                tracker.fail(stage.name, error, rollback_error=rollback_error)
            except Exception as persistence_failure:
                checkpoint_error = persistence_failure
            snapshot = tracker.snapshot
            if checkpoint_error is not None:
                snapshot["checkpoint_persistence_error"] = {
                    "type": type(checkpoint_error).__name__,
                    "message": str(checkpoint_error)[:2000],
                }
            raise RebuildIncompleteError(snapshot) from error

    try:
        tracker.finish()
    except Exception as error:
        snapshot = tracker.snapshot
        snapshot.update(
            status="failed",
            failed_stage="checkpoint_finalization",
            checkpoint_persistence_error={
                "type": type(error).__name__,
                "message": str(error)[:2000],
            },
        )
        raise RebuildIncompleteError(snapshot) from error
    return tracker.snapshot
