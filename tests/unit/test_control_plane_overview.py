from types import SimpleNamespace

from src.control_plane.overview import (
    AI_TASK_TYPES,
    INGESTION_TASK_TYPES,
    _failure_signature,
    _pipeline_stage_status,
)
from src.control_plane.system_resources import system_resources
from src.domain import TaskType


def test_failure_signature_groups_repeated_command_failures_by_first_line() -> None:
    first = SimpleNamespace(
        task_name="Data Release: Site Data Release",
        last_error="Publish downloads failed.\nTraceback A",
    )
    second = SimpleNamespace(
        task_name="Data Release: Site Data Release",
        last_error="Publish downloads failed.\nTraceback B",
    )

    assert _failure_signature(first) == _failure_signature(second)


def test_failure_signature_separates_terminal_exception_causes() -> None:
    push = SimpleNamespace(
        task_name="Data Release: Site Data Release",
        last_error=(
            "Publish downloads failed.\n"
            "Traceback omitted\n"
            "subprocess.CalledProcessError: git push returned 128"
        ),
    )
    manifest = SimpleNamespace(
        task_name="Data Release: Site Data Release",
        last_error=(
            "Publish downloads failed.\n"
            "Traceback omitted\n"
            "RuntimeError: manifest branch does not match"
        ),
    )

    assert _failure_signature(push) != _failure_signature(manifest)


def test_pipeline_status_prefers_recent_failure_over_active_work() -> None:
    assert _pipeline_stage_status(
        INGESTION_TASK_TYPES,
        failed_types={TaskType.CRAWL_DATA},
        active_types={TaskType.CRAWL_DATA},
    ) == "attention"


def test_pipeline_status_does_not_mark_ai_active_for_crawl_work() -> None:
    assert _pipeline_stage_status(
        AI_TASK_TYPES,
        failed_types=set(),
        active_types={TaskType.CRAWL_DATA},
        idle_when_clear=True,
    ) == "idle"


def test_system_resources_snapshot_contains_sidebar_metrics() -> None:
    snapshot = system_resources()

    assert {"cpu", "memory", "disk", "network"}.issubset(snapshot)
    assert snapshot["cpu"]["cores"] >= 0
    assert snapshot["network"]["total"] >= snapshot["network"]["established"]
    assert snapshot["network"]["total"] >= snapshot["network"]["listening"]
    assert set(snapshot["memory"]) == {"total_bytes", "used_bytes", "used_percent"}
    assert set(snapshot["disk"]) == {"total_bytes", "used_bytes", "used_percent"}
