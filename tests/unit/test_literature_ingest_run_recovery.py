from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.domain import Base, LiteratureIngestRun
from src.literature.ingest_run_recovery import reconcile_stale_unbound_runs
from src.services.task_executor import _fail_interrupted_literature_ingest_runs


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


class _AsyncSession:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def commit(self):
        self.session.commit()


def _run(run_uuid: str, *, started_at: datetime, task_uuid: str | None = None):
    return LiteratureIngestRun(
        run_uuid=run_uuid,
        task_uuid=task_uuid,
        source="crossref",
        status="running",
        started_at=started_at,
        checkpoint={},
        counts={},
    )


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[LiteratureIngestRun.__table__])
    with Session(engine) as value:
        yield value


@pytest.mark.asyncio
async def test_legacy_reconciliation_is_dry_run_and_preserves_current_and_bound_runs(session):
    stale = _run("stale-unbound", started_at=NOW - timedelta(hours=4))
    current = _run("current-unbound", started_at=NOW - timedelta(minutes=10))
    bound = _run(
        "stale-but-bound",
        started_at=NOW - timedelta(hours=4),
        task_uuid="replacement-task",
    )
    session.add_all([stale, current, bound])
    session.commit()
    db = _AsyncSession(session)

    plan = await reconcile_stale_unbound_runs(
        db, stale_after_minutes=120, apply=False, now=NOW
    )

    assert plan == {
        "schema_version": 1,
        "mode": "dry_run",
        "stale_after_minutes": 120,
        "eligible_count": 1,
        "updated_count": 0,
        "not_updated_count": 1,
    }
    assert stale.status == current.status == bound.status == "running"

    applied = await reconcile_stale_unbound_runs(
        db, stale_after_minutes=120, apply=True, now=NOW
    )

    assert applied["updated_count"] == 1
    assert stale.status == "failed"
    assert stale.completed_at.replace(tzinfo=timezone.utc) == NOW
    assert stale.error == "stale_unbound_ingest_run"
    assert current.status == "running"
    assert bound.status == "running"


@pytest.mark.asyncio
async def test_task_recovery_finalizes_only_exact_bound_running_attempt(session):
    owned = _run(
        "owned", started_at=NOW - timedelta(hours=1), task_uuid="dead-task"
    )
    replacement = _run(
        "replacement", started_at=NOW - timedelta(hours=1), task_uuid="new-task"
    )
    already_completed = _run(
        "completed", started_at=NOW - timedelta(hours=1), task_uuid="dead-task"
    )
    already_completed.status = "completed"
    already_completed.completed_at = NOW - timedelta(minutes=30)
    session.add_all([owned, replacement, already_completed])
    session.commit()

    changed = await _fail_interrupted_literature_ingest_runs(
        _AsyncSession(session), task_uuid="dead-task", now=NOW
    )
    session.commit()

    assert changed == 1
    assert owned.status == "failed"
    assert owned.error == "task_worker_lease_expired"
    assert owned.checkpoint["task_uuid"] == "dead-task"
    assert replacement.status == "running"
    assert already_completed.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_run_records_exact_task_binding(monkeypatch):
    from src.literature.pipeline import LiteraturePipeline

    added = []

    class _Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    monkeypatch.setattr("src.literature.pipeline.get_db", lambda: _Context())
    config = SimpleNamespace(
        europe_pmc_enabled=False,
        openalex_enabled=False,
        unpaywall_enabled=False,
        publisher_rss_enabled=False,
        springer_nature_enabled=False,
        elsevier_enabled=False,
        preprint_discovery_enabled=False,
        official_guidance_enabled=False,
        controlled_discovery_enabled=False,
        index_overlap_days=2,
    )

    await LiteraturePipeline(config)._create_run(
        "new-run", NOW - timedelta(hours=1), NOW,
        task=SimpleNamespace(task_uuid="exact-task"),
    )

    assert added[0].task_uuid == "exact-task"
    assert added[0].checkpoint["task_uuid"] == "exact-task"
