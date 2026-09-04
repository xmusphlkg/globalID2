import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import LiteratureSettings
from src.domain import Base, LiteratureArticle, LiteratureSummary
from src.services import literature_service as literature_service_module
from src.services.literature_service import (
    LiteratureService,
    _count_catch_up_exception_backlog,
)


def test_literature_capacity_defaults_match_observed_arrival_rate():
    config = LiteratureSettings()

    # Production observed about 300 records per 21 source-index minutes. A
    # 15-minute normal cadence has positive capacity without enlarging the
    # metadata/enrichment batch; truncated checkpoints get a faster follow-up.
    assert config.interval_minutes == 15
    assert config.max_records_per_run == 300
    assert config.index_overlap_days == 0
    assert config.catch_up_enabled is True
    assert config.catch_up_interval_minutes == 5
    assert config.catch_up_max_exception_backlog == 500
    assert config.ai_enrichment_interval_minutes == 15
    assert config.ai_enrichment_catch_up_interval_minutes == 1
    assert config.ai_model_request_timeout_seconds == 35


def test_catch_up_limit_must_leave_room_for_one_bounded_batch():
    with pytest.raises(ValueError, match="must exceed max_records_per_run"):
        LiteratureSettings(
            catch_up_enabled=True,
            catch_up_max_exception_backlog=300,
            max_records_per_run=300,
        )


class _AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


class _AsyncSessionContext:
    def __init__(self, factory):
        self.factory = factory
        self.session = None

    async def __aenter__(self):
        self.session = self.factory()
        return _AsyncSessionAdapter(self.session)

    async def __aexit__(self, _exc_type, _exc, _traceback):
        assert self.session is not None
        self.session.close()
        return False


async def test_catch_up_backlog_counts_distinct_current_review_articles_in_sqlite(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[LiteratureArticle.__table__, LiteratureSummary.__table__],
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add_all([
            LiteratureArticle(
                article_id="review-article",
                slug="review-article",
                title="Review article",
                publication_status="review",
            ),
            LiteratureArticle(
                article_id="summary-only",
                slug="summary-only",
                title="Summary only",
                publication_status="published",
            ),
            LiteratureArticle(
                article_id="article-and-summary",
                slug="article-and-summary",
                title="Article and summary",
                publication_status="review",
            ),
            LiteratureArticle(
                article_id="no-review",
                slug="no-review",
                title="No review",
                publication_status="published",
            ),
        ])
        session.flush()
        session.add_all([
            LiteratureSummary(article_id="summary-only", language="en", status="review"),
            LiteratureSummary(article_id="summary-only", language="zh", status="review"),
            LiteratureSummary(article_id="article-and-summary", language="en", status="review"),
            LiteratureSummary(article_id="no-review", language="en", status="published"),
        ])
        session.commit()

    monkeypatch.setattr(
        literature_service_module,
        "get_database",
        lambda: _AsyncSessionContext(factory),
    )

    assert await _count_catch_up_exception_backlog() == 3
    engine.dispose()


async def test_catch_up_backlog_excludes_only_explicit_deferred_or_archived_work(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[LiteratureArticle.__table__, LiteratureSummary.__table__],
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    with Session(engine) as session:
        session.add_all([
            LiteratureArticle(
                article_id="deferred-both",
                slug="deferred-both",
                title="Deferred article and summary",
                publication_status="review",
                metadata_={"autopilot": {"decision": "defer"}},
            ),
            LiteratureArticle(
                article_id="archived-marker",
                slug="archived-marker",
                title="Archived marker article",
                publication_status="review",
                metadata_={"autopilot": {"decision": "archive"}},
            ),
            LiteratureArticle(
                article_id="deferred-article-active-summary",
                slug="deferred-article-active-summary",
                title="Deferred article with active summary",
                publication_status="review",
                metadata_={"autopilot": {"decision": "defer"}},
            ),
            LiteratureArticle(
                article_id="active-article-deferred-summary",
                slug="active-article-deferred-summary",
                title="Active article with deferred summary",
                publication_status="review",
                metadata_={"autopilot": {"decision": "hold"}},
            ),
            LiteratureArticle(
                article_id="missing-marker",
                slug="missing-marker",
                title="Missing marker article",
                publication_status="review",
                metadata_={},
            ),
            LiteratureArticle(
                article_id="unknown-marker",
                slug="unknown-marker",
                title="Unknown marker article",
                publication_status="review",
                metadata_={"autopilot": {"decision": "new-state"}},
            ),
            LiteratureArticle(
                article_id="archived-summary-status",
                slug="archived-summary-status",
                title="Archived summary status article",
                publication_status="published",
            ),
        ])
        session.flush()
        session.add_all([
            LiteratureSummary(
                article_id="deferred-both",
                language="en",
                status="review",
                generation_metadata={"autopilot": {"decision": "defer"}},
            ),
            LiteratureSummary(
                article_id="archived-marker",
                language="en",
                status="review",
                generation_metadata={"autopilot": {"decision": "archive"}},
            ),
            LiteratureSummary(
                article_id="deferred-article-active-summary",
                language="en",
                status="review",
                generation_metadata={"autopilot": {"decision": "hold"}},
            ),
            LiteratureSummary(
                article_id="active-article-deferred-summary",
                language="en",
                status="review",
                generation_metadata={"autopilot": {"decision": "defer"}},
            ),
            LiteratureSummary(
                article_id="missing-marker",
                language="en",
                status="review",
                generation_metadata={},
            ),
            LiteratureSummary(
                article_id="unknown-marker",
                language="en",
                status="review",
                generation_metadata={"autopilot": {"decision": "new-state"}},
            ),
            LiteratureSummary(
                article_id="archived-summary-status",
                language="en",
                status="archived",
                generation_metadata={},
            ),
        ])
        session.commit()

    monkeypatch.setattr(
        literature_service_module,
        "get_database",
        lambda: _AsyncSessionContext(factory),
    )

    # Fully deferred/archived work is inactive. An active item on either side
    # keeps the article in the union; missing and unknown markers count fail-safe.
    assert await _count_catch_up_exception_backlog() == 4
    engine.dispose()


async def test_truncated_scheduled_task_pulls_persisted_schedule_forward(monkeypatch):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        max_records_per_run=300,
        timezone="UTC",
    )
    service = LiteratureService()
    task = SimpleNamespace(input_data={"scheduled_trigger": True})
    advanced = []

    class FakePipeline:
        def __init__(self, supplied_config):
            assert supplied_config is config

        async def execute(self, supplied_task):
            assert supplied_task is task
            return {
                "source_truncated": 1,
                "source_catch_up_required": 1,
                "source_remaining_index_span_seconds": 7_200,
            }

    async def schedule_earlier(job_kind, job_id, next_run_at):
        advanced.append((job_kind, job_id, next_run_at))
        return True

    async def backlog_count():
        return 100

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module,
        "_count_catch_up_exception_backlog",
        backlog_count,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        schedule_earlier,
    )

    before = datetime.now(timezone.utc)
    result = await service.execute_task(task)
    after = datetime.now(timezone.utc)

    assert result["catch_up_scheduled"] == 1
    assert result["catch_up_paused_backpressure"] == 0
    assert result["catch_up_backlog_observed_count"] == 100
    assert result["catch_up_backlog_projected_upper_bound"] == 400
    assert result["catch_up_resume_below_backlog"] == 200
    assert result["catch_up_status"] == "scheduled"
    assert result["catch_up_next_action_code"] == "await_accelerated_catch_up"
    assert result["catch_up_next_run_at"] is not None
    assert len(advanced) == 1
    job_kind, job_id, next_run_at = advanced[0]
    assert (job_kind, job_id) == ("literature", service.JOB_ID)
    assert before.replace(tzinfo=None) < next_run_at.replace(tzinfo=None)
    assert 4 * 60 <= (next_run_at - after).total_seconds() <= 5 * 60
    assert service._state.next_run_at == next_run_at


async def test_backpressure_pauses_only_accelerated_catch_up_and_keeps_normal_schedule(
    monkeypatch,
):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        max_records_per_run=300,
        timezone="UTC",
    )
    service = LiteratureService()
    normal_next_run = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
    service._state.next_run_at = normal_next_run

    class FakePipeline:
        def __init__(self, _config):
            pass

        async def execute(self, _task):
            return {
                "source_truncated": 1,
                "source_remaining_index_span_seconds": 10_800,
            }

    async def backlog_count():
        return 200

    async def unexpected_schedule(*_args):
        raise AssertionError("backpressure must not schedule a five-minute run")

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module,
        "_count_catch_up_exception_backlog",
        backlog_count,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        unexpected_schedule,
    )

    result = await service.execute_task(
        SimpleNamespace(input_data={"scheduled_trigger": True})
    )

    assert result["catch_up_scheduled"] == 0
    assert result["catch_up_paused_backpressure"] == 1
    assert result["catch_up_backpressure_reason"] == "exception_backlog_headroom_exhausted"
    assert result["catch_up_backlog_observed_count"] == 200
    assert result["catch_up_backlog_projected_upper_bound"] == 500
    assert result["catch_up_backlog_limit"] == 500
    assert result["catch_up_resume_below_backlog"] == 200
    assert result["catch_up_required_backlog_reduction"] == 1
    assert result["catch_up_status"] == "paused_backpressure"
    assert result["catch_up_next_action_code"] == (
        "reduce_exception_backlog_below_resume_threshold"
    )
    assert service._state.next_run_at == normal_next_run


async def test_backlog_measurement_failure_fails_safe_without_accelerated_schedule(monkeypatch):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        max_records_per_run=300,
        timezone="UTC",
    )
    service = LiteratureService()

    class FakePipeline:
        def __init__(self, _config):
            pass

        async def execute(self, _task):
            return {"source_truncated": 1}

    async def failed_backlog_count():
        raise RuntimeError("private database detail")

    async def unexpected_schedule(*_args):
        raise AssertionError("unknown backlog must fail safe")

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module,
        "_count_catch_up_exception_backlog",
        failed_backlog_count,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        unexpected_schedule,
    )

    result = await service.execute_task(
        SimpleNamespace(input_data={"scheduled_trigger": True})
    )

    assert result["catch_up_scheduled"] == 0
    assert result["catch_up_paused_backpressure"] == 1
    assert result["catch_up_backpressure_reason"] == "backlog_measurement_unavailable"
    assert result["catch_up_backlog_observed_count"] is None
    assert result["catch_up_status"] == "paused_backlog_measurement"
    assert result["catch_up_next_action_code"] == "retry_backlog_measurement"
    assert "private database detail" not in str(result)


@pytest.mark.parametrize(
    ("scheduled_trigger", "source_truncated"),
    [(False, 1), (True, 0)],
)
async def test_manual_or_caught_up_task_does_not_pull_schedule_forward(
    monkeypatch,
    scheduled_trigger,
    source_truncated,
):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        timezone="UTC",
    )
    service = LiteratureService()

    class FakePipeline:
        def __init__(self, _config):
            pass

        async def execute(self, _task):
            return {"source_truncated": source_truncated}

    async def unexpected_schedule(*_args):
        raise AssertionError("schedule must not be pulled forward")

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        unexpected_schedule,
    )

    result = await service.execute_task(
        SimpleNamespace(input_data={"scheduled_trigger": scheduled_trigger})
    )

    assert result["catch_up_scheduled"] == 0
    assert "catch_up_next_run_at" not in result


async def test_existing_earlier_catch_up_is_reported_as_durable(monkeypatch):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        max_records_per_run=300,
        timezone="UTC",
    )
    service = LiteratureService()
    persisted_next = datetime.now(timezone.utc)

    class FakePipeline:
        def __init__(self, _config):
            pass

        async def execute(self, _task):
            return {"source_truncated": 1}

    async def backlog_count():
        return 100

    async def not_advanced(*_args):
        return False

    async def load(_kind):
        return {service.JOB_ID: {"next_run_at": persisted_next}}

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module,
        "_count_catch_up_exception_backlog",
        backlog_count,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        not_advanced,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "load",
        load,
    )

    result = await service.execute_task(
        SimpleNamespace(input_data={"scheduled_trigger": True})
    )

    assert result["catch_up_status"] == "already_scheduled"
    assert result["catch_up_next_action_code"] == "await_accelerated_catch_up"
    assert result["catch_up_next_run_at"] == persisted_next.isoformat()


async def test_failed_schedule_persistence_is_not_reported_as_scheduled(monkeypatch):
    config = SimpleNamespace(
        schedule_enabled=True,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        max_records_per_run=300,
        timezone="UTC",
    )
    service = LiteratureService()

    class FakePipeline:
        def __init__(self, _config):
            pass

        async def execute(self, _task):
            return {"source_truncated": 1}

    async def backlog_count():
        return 100

    async def not_advanced(*_args):
        return False

    async def load(_kind):
        return {}

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(literature_service_module, "LiteraturePipeline", FakePipeline)
    monkeypatch.setattr(
        literature_service_module,
        "_count_catch_up_exception_backlog",
        backlog_count,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        not_advanced,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "load",
        load,
    )

    result = await service.execute_task(
        SimpleNamespace(input_data={"scheduled_trigger": True})
    )

    assert result["catch_up_scheduled"] == 0
    assert result["catch_up_status"] == "schedule_persistence_unavailable"
    assert result["catch_up_next_action_code"] == "inspect_scheduler_persistence"


async def test_enrichment_full_batch_schedules_accelerated_catch_up(monkeypatch):
    config = SimpleNamespace(
        ai_enrichment_enabled=True,
        ai_enrichment_schedule_enabled=True,
        ai_enrichment_batch_size=32,
        ai_enrichment_interval_minutes=15,
        ai_enrichment_catch_up_interval_minutes=1,
        weekly_ai_review_enabled=False,
        timezone="UTC",
    )
    service = LiteratureService()
    advanced = []

    class FakeEnrichmentPipeline:
        def __init__(self, supplied_config):
            assert supplied_config is config

        async def execute(self, supplied_task):
            assert supplied_task.input_data["limit"] == 32
            return {
                "articles": 32,
                "generated": 64,
                "skipped": 0,
                "failed": 0,
                "languages": ["en", "zh"],
            }

    async def schedule_earlier(job_kind, job_id, next_run_at):
        advanced.append((job_kind, job_id, next_run_at))
        return True

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(
        literature_service_module,
        "LiteratureEnrichmentPipeline",
        FakeEnrichmentPipeline,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "schedule_earlier",
        schedule_earlier,
    )

    before = datetime.now(timezone.utc)
    result = await service.execute_enrichment_task(
        SimpleNamespace(input_data={"mode": "summaries", "limit": 32})
    )
    after = datetime.now(timezone.utc)

    assert result["ai_enrichment_catch_up_required"] == 1
    assert result["ai_enrichment_catch_up_scheduled"] == 1
    assert result["ai_enrichment_catch_up_status"] == "scheduled"
    assert result["ai_enrichment_catch_up_next_action_code"] == "await_accelerated_enrichment"
    assert len(advanced) == 1
    job_kind, job_id, next_run_at = advanced[0]
    assert (job_kind, job_id) == ("literature", service.ENRICHMENT_JOB_ID)
    assert before.replace(tzinfo=None) < next_run_at.replace(tzinfo=None)
    assert 50 <= (next_run_at - after).total_seconds() <= 60
    assert service._enrichment_state.next_run_at == next_run_at


async def test_status_snapshot_does_not_roll_forward_overdue_persisted_run(monkeypatch):
    overdue = datetime(2026, 8, 17, 11, 55, tzinfo=timezone.utc)
    config = SimpleNamespace(
        enabled=True,
        timezone="UTC",
        schedule_enabled=True,
        interval_minutes=15,
        europe_pmc_enabled=True,
        max_records_per_run=300,
        catch_up_enabled=True,
        catch_up_interval_minutes=5,
        catch_up_max_exception_backlog=500,
        ai_enrichment_enabled=False,
        ai_enrichment_languages=["en", "zh"],
        ai_enrichment_batch_size=10,
        ai_enrichment_schedule_enabled=False,
        weekly_ai_review_enabled=False,
        weekly_ai_review_batch_size=5,
        gap_discovery_enabled=False,
        gap_discovery_schedule_enabled=False,
        gap_discovery_interval_minutes=60,
        gap_discovery_max_gaps_per_run=5,
        gap_discovery_records_per_gap=10,
        gap_discovery_candidate_limit=20,
        autopilot_enabled=False,
    )
    service = LiteratureService()
    saves = []

    async def load(_kind):
        return {
            service.JOB_ID: {
                "next_run_at": overdue,
                "last_status": "completed",
            }
        }

    async def save(*args):
        saves.append(args)

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, _statement):
            return Result()

    class DatabaseContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "load",
        load,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "save",
        save,
    )
    monkeypatch.setattr(literature_service_module, "get_database", DatabaseContext)

    snapshot = await service.snapshot_async()

    core_job = next(job for job in snapshot["jobs"] if job["job_id"] == service.JOB_ID)
    assert core_job["next_run_at"] == overdue.isoformat()
    assert saves == []


async def test_scheduler_restart_restores_persisted_overdue_next_run(monkeypatch):
    overdue = datetime(2026, 8, 17, 11, 55, tzinfo=timezone.utc)
    config = SimpleNamespace(
        enabled=True,
        schedule_enabled=True,
        gap_discovery_schedule_enabled=False,
        ai_enrichment_enabled=False,
        weekly_ai_review_enabled=False,
        ai_enrichment_schedule_enabled=False,
    )
    service = LiteratureService()

    async def load(_kind):
        return {
            service.JOB_ID: {
                "next_run_at": overdue,
                "last_status": "completed",
            }
        }

    saved = {}

    async def save(_kind, job_id, state):
        saved[job_id] = state.next_run_at

    async def no_op_run_loop():
        return None

    monkeypatch.setattr(service, "_config", lambda: config)
    monkeypatch.setattr(service, "_run_loop", no_op_run_loop)
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "load",
        load,
    )
    monkeypatch.setattr(
        literature_service_module.schedule_state_repository,
        "save",
        save,
    )

    await service.start()
    await asyncio.sleep(0)
    await service.stop()

    assert service._state.next_run_at == overdue
    assert saved[service.JOB_ID] == overdue
