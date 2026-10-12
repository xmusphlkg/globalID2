from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from src.generation.report_v4.dataset import SourcePolicy
from src.generation.report_v4.evidence import build_evidence_packet
from src.generation.report_v4.exporters import ReportV4FileExporter
from src.generation.report_v4.surveillance_snapshot import (
    build_surveillance_snapshot,
    ensure_snapshot_matches_evidence,
)
from src.generation.site_data_queries import compact_report_metadata


def test_snapshot_freezes_full_report_series_and_preserves_missing_deaths() -> None:
    months = pd.date_range("2020-01-01", periods=60, freq="MS", tz="UTC")
    frame = pd.DataFrame(
        {
            "disease_id": [1] * len(months),
            "time": months,
            "cases": list(range(len(months))),
            "deaths": [pd.NA] * len(months),
            "incidence_rate": [pd.NA] * len(months),
        }
    )
    period_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2024, 12, 31, tzinfo=timezone.utc)
    kwargs = {
        "diseases": {1: {"code": "D001", "name_en": "Example", "name_zh": "示例"}},
        "period_start": period_start,
        "period_end": period_end,
    }
    snapshot = build_surveillance_snapshot(frame, **kwargs)
    series = snapshot["series"]["D001"]
    assert snapshot["schema_version"] == "report_surveillance.v1"
    assert len(series["dates"]) == 60
    assert series["dates"][-1] == "2024-12-01"
    assert series["cases"][0] == 0
    assert series["deaths"] == [None] * 60
    assert series["total_deaths"] is None
    assert (
        snapshot["version"]
        == build_surveillance_snapshot(frame.copy(), **kwargs)["version"]
    )
    frame.loc[0, "cases"] = 999
    assert (
        snapshot["version"] != build_surveillance_snapshot(frame, **kwargs)["version"]
    )
    packet = {"diseases": [{"disease_id": "D001", "metrics": {"total_cases": 1770}}]}
    ensure_snapshot_matches_evidence(snapshot, packet)
    packet["diseases"][0]["metrics"]["total_cases"] = 1771
    with pytest.raises(ValueError, match="D001"):
        ensure_snapshot_matches_evidence(snapshot, packet)


def test_snapshot_aggregates_report_periods_and_excludes_other_dates() -> None:
    frame = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-05", "cases": 2, "deaths": None},
            {"disease_id": 1, "time": "2026-01-12", "cases": 3, "deaths": None},
            {"disease_id": 1, "time": "2026-02-05", "cases": 5, "deaths": None},
            {"disease_id": 1, "time": "2026-03-05", "cases": 7, "deaths": None},
        ]
    )
    snapshot = build_surveillance_snapshot(
        frame,
        diseases={1: {"code": "D001"}},
        period_start="2026-01-01",
        period_end="2026-02-28",
    )
    series = snapshot["series"]["D001"]
    assert series["dates"] == ["2026-01-05", "2026-01-12", "2026-02-05"]
    assert series["cases"] == [2, 3, 5]
    assert series["total_cases"] == 10


def test_weekly_snapshot_carries_exact_iso_week_boundaries() -> None:
    frame = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2026-01-05", "cases": 2},
            {"disease_id": 1, "time": "2026-01-12", "cases": 3},
        ]
    )
    snapshot = build_surveillance_snapshot(
        frame,
        diseases={1: {"code": "D001"}},
        period_start="2026-01-01",
        period_end="2026-01-31",
    )
    series = snapshot["series"]["D001"]
    assert series["period_granularity"] == "weekly"
    assert series["calendar"] == "iso-week"
    assert series["period_starts"] == ["2026-01-05", "2026-01-12"]
    assert series["period_ends_exclusive"] == ["2026-01-12", "2026-01-19"]


def test_declared_annual_source_is_grouped_as_a_calendar_year() -> None:
    frame = pd.DataFrame(
        [
            {
                "disease_id": 1,
                "time": "2025-03-01",
                "cases": 2,
                "metadata": {"frequency": "annual"},
            },
            {
                "disease_id": 1,
                "time": "2025-09-01",
                "cases": 3,
                "metadata": {"frequency": "annual"},
            },
        ]
    )
    series = build_surveillance_snapshot(
        frame,
        diseases={1: {"code": "D001"}},
        period_start="2025-03-01",
        period_end="2025-12-31",
    )["series"]["D001"]
    assert series["period_granularity"] == "annual"
    assert series["dates"] == ["2025-01-01"]
    assert series["period_ends_exclusive"] == ["2026-01-01"]
    assert series["cases"] == [5]
    assert series["point_quality_statuses"] == ["partial_report_window"]


def test_single_observation_without_cadence_stays_unknown() -> None:
    frame = pd.DataFrame([{"disease_id": 1, "time": "2025-09-15", "cases": 3}])
    series = build_surveillance_snapshot(
        frame,
        diseases={1: {"code": "D001"}},
        period_start="2025-01-01",
        period_end="2025-12-31",
    )["series"]["D001"]
    assert series["period_granularity"] == "unknown"
    assert series["period_starts"] == [None]
    assert series["period_ends_exclusive"] == [None]
    assert series["point_quality_statuses"] == [""]


def test_annual_report_evidence_and_frozen_curve_share_the_same_total() -> None:
    frame = pd.DataFrame(
        [
            {"disease_id": 1, "time": f"{year}-01-01", "cases": value, "deaths": None}
            for year, value in [(2023, 2), (2024, 3), (2025, 4)]
        ]
    )
    diseases = {1: {"code": "D001", "name_en": "Example", "name_zh": "示例"}}
    period_start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2025, 12, 31, tzinfo=timezone.utc)
    packet = build_evidence_packet(
        data=frame,
        historical_data=frame,
        country={"id": 1, "code": "JP", "name": "Japan"},
        diseases=diseases,
        period_start=period_start,
        period_end=period_end,
        source_policy=SourcePolicy(death_counts="not_reported"),
    )
    snapshot = build_surveillance_snapshot(
        frame,
        diseases=diseases,
        period_start=period_start,
        period_end=period_end,
    )
    ensure_snapshot_matches_evidence(snapshot, packet)
    assert snapshot["series"]["D001"]["period_granularity"] == "annual"
    assert snapshot["series"]["D001"]["total_cases"] == 9


def test_mixed_cadences_use_the_same_disease_grain_in_evidence_and_snapshot() -> None:
    frame = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2025-03-01", "cases": 2, "metadata": {"frequency": "annual"}},
            {"disease_id": 1, "time": "2025-09-01", "cases": 3, "metadata": {"frequency": "annual"}},
            {"disease_id": 2, "time": "2025-03-01", "cases": 4, "metadata": {"frequency": "monthly"}},
            {"disease_id": 2, "time": "2025-04-01", "cases": 5, "metadata": {"frequency": "monthly"}},
        ]
    )
    diseases = {1: {"code": "D001"}, 2: {"code": "D002"}}
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2025, 12, 31, tzinfo=timezone.utc)
    packet = build_evidence_packet(
        data=frame,
        historical_data=frame,
        country={"id": 1, "code": "JP", "name": "Japan"},
        diseases=diseases,
        period_start=period_start,
        period_end=period_end,
        source_policy=SourcePolicy(death_counts="not_reported"),
    )
    snapshot = build_surveillance_snapshot(
        frame, diseases=diseases, period_start=period_start, period_end=period_end
    )
    ensure_snapshot_matches_evidence(snapshot, packet)
    evidence = {row["disease_id"]: row for row in packet["diseases"]}
    assert packet["reporting_cadence"] == "mixed"
    assert evidence["D001"]["reporting_cadence"] == "annual"
    assert evidence["D001"]["observation_count"] == 1
    assert evidence["D002"]["reporting_cadence"] == "monthly"
    assert evidence["D002"]["observation_count"] == 2
    assert snapshot["series"]["D001"]["dates"] == ["2025-01-01"]
    assert snapshot["series"]["D002"]["dates"] == ["2025-03-01", "2025-04-01"]


def test_conflicting_cadence_declarations_fail_instead_of_silent_regrouping() -> None:
    frame = pd.DataFrame(
        [
            {"disease_id": 1, "time": "2025-01-01", "cases": 2, "metadata": {"frequency": "annual"}},
            {"disease_id": 1, "time": "2025-02-01", "cases": 3, "metadata": {"frequency": "monthly"}},
        ]
    )
    with pytest.raises(ValueError, match="Conflicting declared reporting frequencies"):
        build_surveillance_snapshot(
            frame,
            diseases={1: {"code": "D001"}},
            period_start="2025-01-01",
            period_end="2025-12-31",
        )


def test_site_report_detail_exports_snapshot_only_for_detail() -> None:
    document = {"schema_version": "report_v4.0", "metrics": {}}
    snapshot = {
        "schema_version": "report_surveillance.v1",
        "version": "abc",
        "series": {},
    }
    metadata = {"report_document_v4": document, "surveillance_snapshot": snapshot}
    assert "surveillance_snapshot" not in compact_report_metadata(metadata)
    assert (
        compact_report_metadata(metadata, include_figures=True)["surveillance_snapshot"]
        == snapshot
    )


def test_archived_report_json_keeps_the_snapshot(tmp_path) -> None:
    class Formatter:
        def format_markdown(self, sections, metadata):
            return "report"

        def format_html(self, sections, metadata):
            return "<p>report</p>"

        def save(self, contents, path):
            from pathlib import Path

            Path(path).write_text(contents, encoding="utf-8")

    report = SimpleNamespace(
        id=123,
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )
    snapshot = {"schema_version": "report_surveillance.v1", "version": "a" * 64}
    document = {
        "title": {"zh": "报告"},
        "summary": {"zh": "摘要"},
        "sections": [],
        "metrics": {},
    }
    ReportV4FileExporter(formatter=Formatter()).write_files(
        report=report,
        output_dir=tmp_path,
        document=document,
        surveillance_snapshot=snapshot,
    )
    archived = json.loads(
        next(tmp_path.glob("*.report-v4.json")).read_text(encoding="utf-8")
    )
    assert archived["surveillance_snapshot"] == snapshot
