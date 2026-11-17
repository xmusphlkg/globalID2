"""Province gap selection remembers collection attempts even if parsing fails."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.automation_service import AutomationJobConfig, _ingestion_job_health
from src.services.crawl_service import CrawlService


async def test_backfill_state_reads_all_recent_tasks_and_collect_artifacts(monkeypatch):
    recent = datetime.now(timezone.utc)
    previous = [SimpleNamespace(
        country_id=7, created_at=recent, task_uuid=f"task-{index}",
        input_data={"source": "cn_province_monthly_report"},
        metadata_={"province_ingestion": {"source_audit": {"history_backfill": {
            "selected_months": ["2022-03"] if index == 129 else [],
        }}}},
    ) for index in range(130)]
    artifact = SimpleNamespace(metadata_={"history_backfill": {"selected_months": ["2022-04"]}})
    statements = []

    async def execute(statement, *args):
        statements.append(statement)
        if len(statements) == 1:
            return SimpleNamespace(scalars=lambda: iter(["2022-01"]))
        values = previous if len(statements) == 2 else [artifact]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: values))

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr("src.core.database.get_db", database)
    known, attempted = await CrawlService._cn_province_backfill_state(
        SimpleNamespace(country_id=7), "CN-BJ"
    )
    assert known == {"2022-01"}
    assert attempted == {"2022-03", "2022-04"}
    assert statements[1]._limit_clause is None
    artifact_sql = str(statements[2])
    assert "source_artifacts.created_at" in artifact_sql
    assert "tasks.created_at" not in artifact_sql


def test_recent_success_with_old_explicit_case_month_is_stale_source():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    task = SimpleNamespace(status="completed", completed_at=now, metadata_={
        "province_ingestion": {"status": "parsed", "source_audit": {
            "latest_report_month": "2026-01", "latest_discovered_month": "2026-10",
        }},
    })
    job = AutomationJobConfig(job_id="cn-bj", name="Beijing", country_code="CN-BJ",
                              source="cn_province_monthly_report", interval_minutes=10080)
    health = _ingestion_job_health(job, task, task, now=now)
    assert health["health_status"] == "stale_source"
    assert "2026-01" in health["health_reason"]
    assert "not an official publication deadline" in health["health_reason"]


def test_image_review_health_reason_does_not_claim_revision_candidates():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    task = SimpleNamespace(status="completed", completed_at=now, metadata_={
        "province_ingestion": {"status": "needs_review", "revision_candidates": [],
                               "source_audit": {"image_reviews": [
                                   {"status": "needs_review"}, {"status": "validated"},
                               ]}},
    })
    job = AutomationJobConfig(job_id="cn-ah", name="Anhui", country_code="CN-AH",
                              source="cn_province_monthly_report", interval_minutes=10080)
    health = _ingestion_job_health(job, task, task, now=now)
    assert health["health_status"] == "needs_review"
    assert "1 province image reports" in health["health_reason"]
    assert "revision candidates" not in health["health_reason"]
