import pytest
from fastapi import HTTPException

from dashboard.api.enum_utils import parse_enum_csv, parse_enum_member
from src.domain.report import ReportStatus, ReportType
from src.domain.task import TaskStatus, TaskType


def test_parse_enum_member_accepts_names_and_values() -> None:
    assert parse_enum_member(TaskStatus, "queued", "status") is TaskStatus.QUEUED
    assert parse_enum_member(TaskStatus, "QUEUED", "status") is TaskStatus.QUEUED
    assert parse_enum_member(TaskType, "update_disease_knowledge", "task_type") is TaskType.UPDATE_DISEASE_KNOWLEDGE
    assert parse_enum_member(TaskType, "UPDATE_DISEASE_KNOWLEDGE", "task_type") is TaskType.UPDATE_DISEASE_KNOWLEDGE
    assert parse_enum_member(ReportType, "weekly", "report_type") is ReportType.WEEKLY
    assert parse_enum_member(ReportStatus, "COMPLETED", "status") is ReportStatus.COMPLETED


def test_parse_enum_csv_accepts_mixed_name_value_tokens() -> None:
    assert parse_enum_csv(TaskStatus, "queued,RUNNING", "status") == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ]


def test_parse_enum_member_rejects_unknown_values() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_enum_member(TaskStatus, "readyish", "status")

    assert exc_info.value.status_code == 422
    assert "readyish" in str(exc_info.value.detail)
