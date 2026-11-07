"""Compact publication classification from the task's frozen execution inputs."""
from __future__ import annotations

from typing import Any, Literal

PublicationKind = Literal[
    "static", "surveillance_json", "situation_json", "unknown_json",
    "reports", "code", "raw_archive",
]


def publication_kind(task_type: Any, input_data: Any) -> PublicationKind | None:
    if getattr(task_type, "value", task_type) != "export_data":
        return None
    data = input_data if isinstance(input_data, dict) else {}
    if data.get("data_only") is True:
        line = data.get("data_line") or "surveillance"
        if line == "surveillance":
            return "surveillance_json"
        if line == "situation":
            return "situation_json"
        return "unknown_json"
    if data.get("raw_archive_only") is True:
        return "raw_archive"
    if data.get("reports_only") is True:
        return "reports"
    if data.get("code_only") is True:
        return "code"
    return "static"
