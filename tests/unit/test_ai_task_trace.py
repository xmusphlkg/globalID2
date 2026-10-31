"""AI task progress and the log lines synthesized for the control panel."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.ai.task_progress import displayed_progress
from src.control_plane.ai_task_trace import build_ai_task_entries


def _item(**kwargs):
    base = dict(
        id=7,
        line="agent_workflow",
        status="failed",
        attempt=2,
        route_key="kiro:hy3",
        error="TimeoutError: model timed out",
        payload={"agent_name": "content_governance_reviewer"},
        created_at=datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 31, 8, 1, tzinfo=timezone.utc),
        lease_expires_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_running_agent_workflow_uses_model_call_progress():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 8, "leased": 2, "queued": 10},
        )
        == 40
    )


def test_running_agent_workflow_does_not_show_complete_before_the_parent_finishes():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 4},
        )
        == 99
    )


def test_completed_model_operation_without_stored_progress_reads_as_done():
    assert (
        displayed_progress(
            task_type="AGENT_WORKFLOW",
            status="COMPLETED",
            stored=0,
            counts={},
        )
        == 100
    )


def test_literature_progress_already_recorded_by_the_service_stays_put():
    assert (
        displayed_progress(
            task_type="enrich_literature",
            status="running",
            stored=80,
            counts={"succeeded": 1, "queued": 9},
        )
        == 80
    )


def test_failed_knowledge_reconcile_uses_stage_progress_when_no_model_call_ran():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="failed",
            stored=0,
            counts={},
            stages={"completed": 2, "failed": 1},
        )
        == 100
    )


def test_running_knowledge_reconcile_stops_short_of_100_until_the_parent_finishes():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={},
            stages={"completed": 1, "running": 1},
        )
        == 50
    )


def test_model_call_progress_wins_over_stage_rows():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="running",
            stored=0,
            counts={"succeeded": 1, "queued": 3},
            stages={"completed": 3},
        )
        == 25
    )


def test_failed_agent_workflow_shows_how_far_the_model_calls_got():
    assert (
        displayed_progress(
            task_type="agent_workflow",
            status="failed",
            stored=0,
            counts={"succeeded": 1, "failed": 1, "queued": 2},
        )
        == 50
    )


def test_trace_includes_stage_metrics_model_failures_and_result():
    now = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
    entries = build_ai_task_entries(
        task_type="agent_workflow",
        status="running",
        input_data={"operation": "governance_stream"},
        output_data={"result": {"claimed": 4, "published": 1, "rejected": 0, "deferred": 1}},
        completed_at=None,
        counts={"succeeded": 3, "failed": 1, "leased": 1},
        items=[
            _item(),
            _item(
                id=8,
                status="leased",
                error=None,
                finished_at=None,
                lease_expires_at=now + timedelta(minutes=10),
            ),
        ],
        stages=[
            SimpleNamespace(
                stage="store",
                status="completed",
                attempt=1,
                metrics={"source_slots": 6, "content_gap_created": 0},
                error=None,
                started_at=now - timedelta(seconds=30),
                finished_at=now - timedelta(seconds=5),
            ),
            SimpleNamespace(
                stage="collect",
                status="skipped",
                attempt=1,
                metrics={},
                error=None,
                started_at=now,
                finished_at=None,
            ),
        ],
        now=now,
    )
    titles = [entry["title"] for entry in entries]
    assert "Stage store completed" in titles
    assert "Stage collect skipped" not in titles
    assert any(entry["title"] == "Model call failed" and entry["entry_type"] == "error" for entry in entries)
    assert any(entry["title"] == "Model call leased" for entry in entries)
    progress = next(entry for entry in entries if entry["title"] == "Model work progress")
    assert "operation: governance_stream" in progress["content"]
    assert "failed: 1" in progress["content"]
    assert progress["entry_type"] == "warning"
    result = next(entry for entry in entries if entry["title"] == "Task result")
    assert "claimed: 4" in result["content"]
    stage = next(entry for entry in entries if entry["title"] == "Stage store completed")
    assert "source_slots: 6" in stage["content"]


def test_non_ai_task_without_model_work_adds_no_log():
    assert (
        build_ai_task_entries(
            task_type="crawl_data",
            status="completed",
            input_data={},
            output_data={"total_records": 3},
            completed_at=None,
            counts={},
            items=[],
            stages=[],
        )
        == []
    )
