"""Isolated, bounded replay of failed Crossref journal windows.

Replay uses the regular normalization, evidence capture and transactions, but
never reads or replaces the live synchronization watermark. Each worker task
processes one bounded page; a later task resumes this window's own checkpoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.core.database import get_db
from src.domain import LiteratureIngestRun, Task

from .collection_sources import COLLECTION_SOURCES
from .pipeline import LiteraturePipeline, _parse_checkpoint_datetime

REPLAY_SOURCE = "literature-crossref-replay"


@dataclass(frozen=True)
class ReplayWindow:
    origin_run_uuid: str
    since: datetime
    until: datetime
    issns: tuple[str, ...]

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> ReplayWindow:
        since = _parse_checkpoint_datetime(data.get("since"))
        until = _parse_checkpoint_datetime(data.get("until"))
        raw = data.get("issns")
        if not isinstance(raw, list) or not raw or any(not isinstance(v, str) or not v.strip() for v in raw):
            raise ValueError("Replay requires a non-empty list of journal ISSNs")
        origin = str(data.get("origin_run_uuid") or "").strip()
        if not origin or since is None or until is None or since >= until:
            raise ValueError("Replay requires an origin run and an ordered timestamp window")
        if until > datetime.now(timezone.utc):
            raise ValueError("Replay cannot include a future window")
        return cls(origin, since.astimezone(timezone.utc), until.astimezone(timezone.utc), tuple(sorted({v.strip() for v in raw})))

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin_run_uuid": self.origin_run_uuid,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "issns": list(self.issns),
        }

    @property
    def key(self) -> str:
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def failed_journal_windows(runs: list[Any]) -> list[ReplayWindow]:
    """Derive scope only from persisted journal errors, never whole-corpus since."""
    windows: dict[str, ReplayWindow] = {}
    for run in runs:
        if run.status != "completed" or not str(run.source).startswith("crossref"):
            continue
        cp = run.checkpoint if isinstance(run.checkpoint, dict) else {}
        # New safe-watermark failures remain in the normal retry loop.
        if cp.get("watermark_held_for_errors"):
            continue
        journals = cp.get("journals") or []
        failed = [str(j["issn"]) for j in journals if isinstance(j, dict) and j.get("error") and j.get("issn")]
        if not failed:
            continue
        since = cp.get("from_indexed_at") or run.from_indexed_at
        until = cp.get("requested_through_indexed_at") or run.through_indexed_at
        window = ReplayWindow.parse({
            "origin_run_uuid": run.run_uuid,
            "since": since.isoformat() if isinstance(since, datetime) else since,
            "until": until.isoformat() if isinstance(until, datetime) else until,
            "issns": failed,
        })
        windows[window.key] = window
    return list(windows.values())


class LiteratureReplayPipeline(LiteraturePipeline):
    def __init__(self, config: Any, window: ReplayWindow) -> None:
        # Only journal collection is replayed. Normal metadata enrichment and
        # publication gates remain enabled according to the current policy.
        updates = {spec.flag: False for spec in COLLECTION_SOURCES if spec.flag}
        updates["payload_compaction_enabled"] = False
        isolated = config.model_copy(update=updates)
        super().__init__(isolated)
        self.window = window

    async def execute(self, task: Task | None = None) -> dict[str, Any]:
        # Only the task identity is inherited. Generic workflow inputs must not
        # select providers or rewrite the isolated replay's source label.
        scoped_task = Task(task_uuid=task.task_uuid, input_data={}) if task is not None else None
        return await super().execute(scoped_task)

    async def _create_run(
        self, run_uuid: str, since: datetime, through: datetime, *, task: Task | None = None,
    ) -> None:
        # Store recovery scope in the initial transaction, including attempts
        # interrupted before _finish_run can run.
        async with get_db() as db:
            db.add(LiteratureIngestRun(
                run_uuid=run_uuid,
                task_uuid=task.task_uuid if task is not None else None,
                source=REPLAY_SOURCE,
                status="running",
                started_at=datetime.now(timezone.utc),
                from_indexed_at=since,
                through_indexed_at=through,
                checkpoint={
                    "strategy": "index-date",
                    "replay_key": self.window.key,
                    "replay_window": self.window.as_dict(),
                    "task_uuid": task.task_uuid if task is not None else None,
                },
                counts={},
            ))
            await db.commit()
        from .source_log import log_literature_event

        await log_literature_event(
            task.task_uuid if task is not None else None,
            title="Literature journal replay started",
            content=f"Replaying {len(self.window.issns)} journal(s) in a fixed historical window.",
            metadata={"event": "literature_replay", "replay_key": self.window.key, **self.window.as_dict()},
        )

    def _collection_until(self, now: datetime) -> datetime:
        return self.window.until

    def _collection_journals(self) -> list[dict[str, Any]]:
        journals = super()._collection_journals()
        allowed = {str(j["issn"]) for j in journals}
        if not set(self.window.issns) <= allowed:
            raise ValueError("Replay contains a journal outside the configured registry")
        return [j for j in journals if str(j["issn"]) in self.window.issns]

    def _source_label(self, **_kwargs: Any) -> str:
        return REPLAY_SOURCE

    async def _resolve_start(self, now: datetime, task: Task | None):
        # Task input cannot override an audited replay window with a live since.
        snapshot = await self._load_latest_completed_checkpoint()
        cp = snapshot["checkpoint"] if snapshot else {}
        since = _parse_checkpoint_datetime(cp.get("next_from_indexed_at"))
        if since is not None and self.window.since <= since <= self.window.until:
            resume = cp.get("resume_after")
            return since, resume if isinstance(resume, dict) else None
        return self.window.since, None

    async def _load_latest_completed_checkpoint(self):
        if not self._latest_completed_checkpoint_loaded:
            async with get_db() as db:
                row = (await db.execute(
                    select(LiteratureIngestRun)
                    .where(
                        LiteratureIngestRun.source == REPLAY_SOURCE,
                        LiteratureIngestRun.status == "completed",
                        LiteratureIngestRun.checkpoint["replay_key"].as_string() == self.window.key,
                    )
                    .order_by(LiteratureIngestRun.completed_at.desc(), LiteratureIngestRun.id.desc())
                    .limit(1)
                )).scalar_one_or_none()
            if row is not None:
                self._latest_completed_checkpoint = {
                    "through_indexed_at": row.through_indexed_at,
                    "checkpoint": row.checkpoint,
                }
            self._latest_completed_checkpoint_loaded = True
        return self._latest_completed_checkpoint

    async def _finish_run(self, run_uuid: str, status: str, **kwargs: Any) -> None:
        kwargs["source"] = REPLAY_SOURCE
        kwargs["checkpoint"] = {
            **(kwargs.get("checkpoint") or {}),
            "replay_key": self.window.key,
            "replay_window": self.window.as_dict(),
        }
        await super()._finish_run(run_uuid, status, **kwargs)


async def execute_replay(arguments: dict[str, Any]) -> dict[str, Any]:
    """Worker-only entry point: current task owns stages, artifacts and writes."""
    from src.ai.runtime import current_parent
    from src.core.config import get_config

    parent = current_parent()
    if parent is None:
        raise RuntimeError("Literature replay requires a worker task")
    window = ReplayWindow.parse(arguments)
    config = get_config().literature
    if not config.enabled:
        raise ValueError("Research Radar is disabled")
    async with get_db() as db:
        task = (await db.execute(select(Task).where(Task.task_uuid == parent.task_uuid))).scalar_one()
    pipeline = LiteratureReplayPipeline(config, window)
    # Validate scope before an ingest audit row is created.
    pipeline._collection_journals()
    snapshot = await pipeline._load_latest_completed_checkpoint()
    cp = snapshot["checkpoint"] if snapshot else {}
    through = _parse_checkpoint_datetime(cp.get("through_indexed_at"))
    if through is not None and through >= window.until and not cp.get("truncated") and not cp.get("source_errors"):
        return {"replay_key": window.key, "status": "already_completed", "replay_complete": True}
    result = await pipeline.execute(task)
    return {**result, "replay_key": window.key, "replay_complete": not bool(result.get("source_truncated"))}
