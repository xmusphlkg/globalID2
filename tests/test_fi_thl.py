from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

from src.data.crawlers.fi import (
    DEFAULT_CUBE_URL,
    FICubeDataError,
    FinlandTHLCrawler,
    parse_dimension_catalog,
    parse_monthly_csv,
    recent_closed_months,
)
from src.data.processors.fi import (
    FIReportingGroupCollisionError,
    FIMonthlyUpdater,
    build_legacy_projection,
)


def _node(
    node_id: str,
    sid: int,
    label: str,
    code: str,
    stage: str,
    children: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    node: dict[str, object] = {
        "id": node_id,
        "sid": sid,
        "label": label,
        "code": code,
        "stage": stage,
    }
    if children is not None:
        node["children"] = children
    return node


def _dimension_payload() -> str:
    dimensions = [
        {
            "id": "nidrreportgroup",
            "children": [
                _node(
                    "nidrreportgroup1",
                    100,
                    "All reporting groups",
                    "allreportgroups",
                    "root",
                    [
                        _node(
                            "nidrreportgroup2",
                            101,
                            "Tuberculosis, total",
                            "700",
                            "reportgroup",
                        ),
                        _node(
                            "nidrreportgroup3",
                            102,
                            "Pulmonary tuberculosis",
                            "720",
                            "reportgroup",
                        ),
                    ],
                )
            ],
        },
        {
            "id": "yearmonth",
            "children": [
                _node(
                    "yearmonth1",
                    200,
                    "All years",
                    "alltime",
                    "root",
                    [
                        _node(
                            "yearmonth2",
                            201,
                            "Year 1995",
                            "1995",
                            "year",
                            [
                                _node(
                                    "yearmonth2-1",
                                    202,
                                    "January 1995",
                                    "1995-01",
                                    "month",
                                )
                            ],
                        ),
                        _node(
                            "yearmonth3",
                            203,
                            "Year 2026",
                            "2026",
                            "year",
                            [
                                _node(
                                    "yearmonth3-1",
                                    204,
                                    "January 2026",
                                    "2026-01",
                                    "month",
                                ),
                                _node(
                                    "yearmonth3-7",
                                    205,
                                    "July 2026",
                                    "2026-07",
                                    "month",
                                ),
                                _node(
                                    "yearmonth3-8",
                                    206,
                                    "August 2026",
                                    "2026-08",
                                    "month",
                                ),
                            ],
                        ),
                    ],
                )
            ],
        },
        {
            "id": "wscmunicipality2022",
            "children": [
                _node("area1", 300, "All areas", "Area", "root", [])
            ],
        },
        {
            "id": "nidragegroup",
            "children": [
                _node("age1", 400, "All ages", "allages", "root", [])
            ],
        },
        {
            "id": "nidrsex",
            "children": [
                _node("sex1", 500, "All sexes", "allsexes", "root", [])
            ],
        },
        {
            "id": "measure",
            "children": [
                _node(
                    "measure",
                    600,
                    "Measure",
                    "measure",
                    "root",
                    [_node("measure/CASES", 601, "Cases", "", "leaf")],
                )
            ],
        },
    ]
    return f"thl.pivot.loadDimensions({json.dumps(dimensions)});"


def _cube_csv() -> str:
    return (
        "Time;Reporting group;val\n"
        'July 2026;"Tuberculosis, total";3\n'
        "July 2026;Pulmonary tuberculosis;0\n"
        'August 2026;"Tuberculosis, total";2\n'
        "August 2026;Pulmonary tuberculosis;1\n"
        'Year 2026;"Tuberculosis, total";5\n'
        'All years;"Tuberculosis, total";8\n'
        "July 2026;All reporting groups;\n"
    )


class _FakeResponse:
    def __init__(self, content: bytes, url: str) -> None:
        self.content = content
        self.url = url
        self.encoding = "utf-8"
        self.headers = {
            "content-type": "text/csv; charset=utf-8",
            "last-modified": "Fri, 07 Aug 2026 08:00:00 GMT",
        }


def test_fi_dimension_discovery_uses_live_catalog_sids() -> None:
    catalog = parse_dimension_catalog(_dimension_payload())

    assert catalog.reporting_group_root.selector == "nidrreportgroup-100"
    assert catalog.year_nodes[2026].selector == "yearmonth-203"
    assert catalog.month_nodes[(1995, 1)].label == "January 1995"
    assert catalog.all_areas.selector == "wscmunicipality2022-300"
    assert catalog.all_ages.selector == "nidragegroup-400"
    assert catalog.all_sexes.selector == "nidrsex-500"
    assert catalog.cases.selector == "measure-601"


def test_fi_csv_keeps_parent_child_and_zero_without_annual_total() -> None:
    catalog = parse_dimension_catalog(_dimension_payload())
    rows = parse_monthly_csv(
        _cube_csv(),
        catalog,
        requested_months=[(2026, 7), (2026, 8)],
        as_of=date(2026, 8, 7),
        query_url="https://example.test/cube.csv?query=1",
        retrieved_at=datetime(2026, 8, 7, 9, tzinfo=timezone.utc),
        response_sha256="abc123",
    )

    assert [(row["Date"], row["DiseaseCode"], row["Cases"]) for row in rows] == [
        ("2026-07-01", "700", "3"),
        ("2026-07-01", "720", "0"),
        ("2026-08-01", "700", "2"),
        ("2026-08-01", "720", "1"),
    ]
    assert {row["DatasetStatus"] for row in rows[:2]} == {"closed_revisable"}
    assert {row["DatasetStatus"] for row in rows[2:]} == {"provisional"}
    assert all(row["GeographyKey"] == "country:FI:national" for row in rows)
    assert all(row["RawSHA256"] == "abc123" for row in rows)
    assert all(json.loads(row["Dimensions"]) == {} for row in rows)


def test_fi_csv_fails_closed_on_unknown_reporting_group() -> None:
    catalog = parse_dimension_catalog(_dimension_payload())
    payload = "Time;Reporting group;val\nJuly 2026;New upstream group;1\n"

    with pytest.raises(FICubeDataError, match="unknown reporting group"):
        parse_monthly_csv(
            payload,
            catalog,
            requested_months=[(2026, 7)],
            as_of=date(2026, 8, 7),
            query_url="https://example.test/cube.csv",
            retrieved_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            response_sha256="abc123",
        )


def test_fi_default_schedule_uses_only_closed_months() -> None:
    assert recent_closed_months(date(2026, 8, 7), 3) == [
        (2026, 5),
        (2026, 6),
        (2026, 7),
    ]
    assert FIMonthlyUpdater()._resolve_requested_months(
        None,
        as_of=date(2026, 8, 7),
        include_provisional=False,
        backfill_history=False,
    ) == [(2026, 5), (2026, 6), (2026, 7)]


def test_fi_dynamic_revision_window_can_include_current_month() -> None:
    updater = FIMonthlyUpdater(
        refresh_recent_months=3,
        include_current_month=True,
    )

    assert updater._resolve_requested_months(
        None,
        as_of=date(2026, 8, 7),
        include_provisional=updater.include_current_month,
        backfill_history=False,
    ) == [(2026, 6), (2026, 7), (2026, 8)]


def test_fi_full_history_queries_year_nodes_not_all_years_root() -> None:
    catalog = parse_dimension_catalog(_dimension_payload())
    requested = sorted(
        FinlandTHLCrawler._eligible_months(
            catalog,
            as_of=date(2026, 8, 7),
            include_provisional=False,
        )
    )

    nodes = FinlandTHLCrawler._query_nodes(
        catalog,
        requested,
        as_of=date(2026, 8, 7),
        include_provisional=False,
    )

    assert [node.code for node in nodes] == ["1995", "2026"]
    assert all(node.sid != catalog.all_time.sid for node in nodes)


def test_fi_crawler_queries_discovered_national_slice_and_archives_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    output_csv = tmp_path / "finland.csv"
    crawler = FinlandTHLCrawler(save_raw=True, raw_dir=raw_dir)
    calls: list[tuple[str, list[tuple[str, str]]]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        params = list(kwargs.get("params") or [])
        calls.append((url, params))
        if url.endswith(".dimensions.json"):
            return _FakeResponse(_dimension_payload().encode(), url)
        return _FakeResponse(
            _cube_csv().encode(), f"{url}?{urlencode(params)}"
        )

    monkeypatch.setattr(crawler, "get", fake_get)
    summary = crawler.crawl_monthly_national(
        output_csv,
        months=[(2026, 7), (2026, 8)],
        as_of=date(2026, 8, 7),
        retrieved_at=datetime(2026, 8, 7, 9, tzinfo=timezone.utc),
    )

    assert summary.row_count == 2
    assert summary.reporting_groups_fetched == 2
    assert summary.latest_date == date(2026, 7, 1)
    assert summary.omitted_provisional_months == 1
    assert calls[1][1] == [
        ("row", "nidrreportgroup-100"),
        ("column", "yearmonth-203"),
        ("filter", "wscmunicipality2022-300"),
        ("filter", "nidragegroup-400"),
        ("filter", "nidrsex-500"),
        ("filter", "measure-601"),
    ]
    assert "requests" not in crawler.session.headers["User-Agent"].casefold()
    assert crawler.session.headers["Referer"] == DEFAULT_CUBE_URL
    assert (raw_dir / "dimensions.jsonp").exists()
    assert (raw_dir / "dimensions.provenance.json").exists()
    assert (raw_dir / "year-2026.csv").exists()
    provenance = json.loads(
        (raw_dir / "year-2026.provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["selectors"]["area"] == "wscmunicipality2022-300"
    assert provenance["sha256"]

    with output_csv.open(encoding="utf-8", newline="") as handle:
        output_rows = list(csv.DictReader(handle))
    assert [(row["DiseaseCode"], row["Cases"]) for row in output_rows] == [
        ("700", "3"),
        ("720", "0"),
    ]
    assert all(row["IsProvisional"] == "false" for row in output_rows)


def test_fi_legacy_projection_is_idempotent_for_exact_source_row() -> None:
    row = {
        "Date": "2026-07-01",
        "RawDiseaseLabel": "Tuberculosis, total",
        "DiseaseCode": "700",
        "ReportingGroupSID": "101",
        "Cases": "3",
        "Geography": "All areas",
        "Age": "All ages",
        "Sex": "All sexes",
        "Measure": "Cases",
    }

    projection = build_legacy_projection(
        [row, dict(row)], {"tuberculosis, total": 25}, country_id=246
    )

    assert len(projection.rows) == 1
    assert projection.rows[0]["cases"] == 3
    assert projection.skipped_unmapped == 0


def test_fi_legacy_projection_refuses_parent_child_collapse() -> None:
    rows = [
        {
            "Date": "2026-07-01",
            "RawDiseaseLabel": "Tuberculosis, total",
            "DiseaseCode": "700",
            "Cases": "3",
        },
        {
            "Date": "2026-07-01",
            "RawDiseaseLabel": "Pulmonary tuberculosis",
            "DiseaseCode": "720",
            "Cases": "2",
        },
    ]

    with pytest.raises(FIReportingGroupCollisionError, match="must not be aggregated"):
        build_legacy_projection(
            rows,
            {
                "tuberculosis, total": 25,
                "pulmonary tuberculosis": 25,
            },
            country_id=246,
        )


def test_fi_mapping_uses_one_legacy_aggregate_and_corrects_cube_mistranslations() -> None:
    mapping_path = Path(__file__).resolve().parents[1] / "configs/mapping/fi.csv"
    with mapping_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_code = {row["local_code"]: row for row in rows}

    assert len(by_code) == len(rows)
    assert by_code["700"]["disease_id"] == "D025"
    assert "720" not in by_code
    assert by_code["1135"]["disease_id"] == "D038"
    assert "1140" not in by_code
    assert by_code["170"]["disease_id"] == "D017"
    assert "Measles" in by_code["170"]["aliases"]
    assert by_code["171"]["disease_id"] == "D039"
    assert "Mumps" in by_code["171"]["aliases"]
    assert {row["source_id"] for row in rows} == {"SRC_FI_THL_TTR"}
    assert all(row["series_id"].startswith("SER_FI_THL_TTR_") for row in rows)
