from datetime import datetime, timezone

import pytest

from src.literature.health_pipeline_checks import _pipeline_checks
from src.literature.health_snapshot import HealthThresholds, ResearchRadarSnapshot


@pytest.mark.parametrize("resume_below, action", [
    (200, "reduce_exception_backlog_below_resume_threshold"),
    (0, "configure_catch_up_backlog_headroom"),
    (-100, "configure_catch_up_backlog_headroom"),
])
def test_health_distinguishes_exhausted_review_capacity_from_impossible_headroom(resume_below, action):
    now = datetime.now(timezone.utc)
    snapshot = ResearchRadarSnapshot(
        collected_at=now,
        ingest_runs=({
            "source": "crossref", "status": "completed", "completed_at": now,
            "through_indexed_at": now,
            "checkpoint": {"strategy": "index-date", "catch_up_required": True},
        },),
        tasks=({
            "type": "sync_literature", "status": "completed",
            "catch_up": {
                "catch_up_status": "paused_backpressure",
                "catch_up_resume_below_backlog": resume_below,
                "catch_up_backlog_counting_basis": "distinct_actionable_review_articles",
            },
        },),
        articles=(), summaries=(), evidence_gaps=(), expected_sources=(),
        release_payload=None, release_read_status="missing",
        backfill_checkpoint=None, backfill_read_status="missing",
    )
    checks = {item["code"]: item for item in _pipeline_checks(snapshot, HealthThresholds())}
    result = checks["catch_up_orchestration"]
    assert result["status"] == "warning"
    assert result["next_action_code"] == action
    assert result["observed"]["resume_below_backlog"] == resume_below
    assert result["observed"]["backlog_counting_basis"] == "distinct_actionable_review_articles"
