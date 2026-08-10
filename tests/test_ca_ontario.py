from __future__ import annotations

import base64
import copy
import csv
import json
import time
from datetime import date, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

import src.data.crawlers.ca as ca_module
from src.data.crawlers.ca import (
    DEFAULT_REPORT_ID,
    DEFAULT_VISUAL_NAME,
    MONTHS,
    ONTARIO_GEOGRAPHY_KEY,
    CanadaOntarioPHOCrawler,
    MonthlyVisual,
    _month_column,
    _content_hash,
    _assert_archive_safe,
    decode_powerbi_dm0,
    discover_monthly_visual,
    extract_embed_context,
    normalize_powerbi_monthly_rows,
    normalize_export_file,
    normalize_export_table,
)
from src.data.processors.ca import CAOntarioMonthlyUpdater
from src.core.country_library import get_country_bootstrap_config
from src.core.source_scopes import (
    default_source_for_country,
    get_expected_scopes_for_country,
    source_scope_label,
)


def _routing_token(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"opaque.{encoded}"


def _title_config(*, name: str, title: str, visual_type: str = "tableEx") -> str:
    return json.dumps(
        {
            "name": name,
            "singleVisual": {
                "visualType": visual_type,
                "vcObjects": {
                    "title": [
                        {
                            "properties": {
                                "text": {
                                    "expr": {"Literal": {"Value": f"'{title}'"}}
                                }
                            }
                        }
                    ]
                },
            },
        }
    )


def _powerbi_monthly_response(
    *,
    disease: str = "Measles",
    month_values: list[object] | None = None,
    ytd_cases: object = 5,
    ytd_rate: object = "0.03",
) -> dict[str, object]:
    values = month_values or [2, 0, 3, *([None] * 9)]
    assert len(values) == 12
    semantic_names = [
        "Lookup Disease.Disease",
        *[
            f"Monthly Data Table Measures.{index:02d} {month_name}"
            for index, (month_name, _month_number) in enumerate(MONTHS, 1)
        ],
        "Monthly Data Table Measures.Cases YTD",
        "Monthly ON + PHU Case.YTDRate",
    ]
    physical_names = [f"D{index}" for index in range(len(semantic_names))]
    encoded_row = {
        "S": [{"N": name} for name in physical_names],
        "C": [disease, *values, ytd_cases, ytd_rate],
    }
    selections = [
        {"Value": physical, "Name": semantic}
        for physical, semantic in zip(physical_names, semantic_names)
    ]
    return {
        "results": [
            {
                "result": {
                    "data": {
                        "timestamp": "2026-08-01T12:34:56Z",
                        "dsr": {"DS": [{"PH": [{"DM0": [encoded_row]}]}]},
                        "descriptor": {"Select": selections},
                    }
                }
            }
        ]
    }


def test_extract_embed_context_decodes_and_validates_routing_payload() -> None:
    token = _routing_token(
        {
            "clusterUrl": "https://wabi-canada-central-api.analysis.windows.net/",
            "exp": int(time.time()) + 3600,
        }
    )
    html = (
        f'<script>accessToken = "{token}"; '
        f'embedReportId = "{DEFAULT_REPORT_ID}";</script>'
    )

    context = extract_embed_context(html, expected_report_id=DEFAULT_REPORT_ID)

    assert context.token == token
    assert context.cluster_url == (
        "https://wabi-canada-central-api.analysis.windows.net"
    )
    assert context.report_id == DEFAULT_REPORT_ID
    assert context.expires_at > int(time.time())
    assert token not in repr(context)


def test_extract_embed_context_rejects_non_powerbi_routing_host() -> None:
    token = _routing_token(
        {
            "clusterUrl": "https://analysis.windows.net.attacker.example",
            "exp": int(time.time()) + 3600,
        }
    )

    with pytest.raises(RuntimeError, match="unexpected Power BI cluster host"):
        extract_embed_context(
            f'accessToken = "{token}";',
            expected_report_id=DEFAULT_REPORT_ID,
        )


def test_discover_monthly_visual_uses_page_type_and_configured_visual_name() -> None:
    expected_query = {"Commands": [{"SemanticQueryDataShapeCommand": {}}]}
    metadata = {
        "models": [
            {
                "id": "3305775",
                "dbName": "dataset-ontario",
                "LastRefreshTime": "2026-08-02T07:00:00Z",
            }
        ],
        "exploration": {
            "sections": [
                {
                    "displayName": "Overview",
                    "visualContainers": [
                        {
                            "config": _title_config(
                                name="overview-chart",
                                title="Overview",
                                visual_type="lineChart",
                            ),
                            "query": json.dumps({"ignore": True}),
                        }
                    ],
                },
                {
                    "displayName": "Monthly Data Table",
                    "visualContainers": [
                        {
                            "config": _title_config(
                                name="wrong-table",
                                title="Rates by geography",
                            ),
                            "query": json.dumps({"wrong": True}),
                        },
                        {
                            "config": _title_config(
                                name=DEFAULT_VISUAL_NAME,
                                title="Disease cases by month 2026",
                            ),
                            "query": json.dumps(expected_query),
                        },
                    ],
                },
            ]
        },
    }

    visual = discover_monthly_visual(metadata)

    assert visual.model_id == 3305775
    assert visual.dataset_id == "dataset-ontario"
    assert visual.query == expected_query
    assert visual.page_name == "Monthly Data Table"
    assert visual.visual_name == DEFAULT_VISUAL_NAME
    assert visual.title == "Disease cases by month 2026"
    assert visual.model_refresh_time == "2026-08-02T07:00:00Z"


def test_decode_powerbi_dm0_expands_repeat_and_null_masks() -> None:
    response = {
        "results": [
            {
                "result": {
                    "data": {
                        "timestamp": "2026-08-03T10:00:00Z",
                        "dsr": {
                            "DS": [
                                {
                                    "PH": [
                                        {
                                            "DM0": [
                                                {
                                                    "S": [
                                                        {"N": "D0"},
                                                        {"N": "D1"},
                                                        {"N": "D2"},
                                                    ],
                                                    "C": ["Measles", 2, 5],
                                                },
                                                {
                                                    # Repeat disease (bit 0),
                                                    # set Jan to null (bit 1),
                                                    # and consume one value for Feb.
                                                    "R": 0b001,
                                                    "Ø": 0b010,
                                                    "C": [7],
                                                },
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                        "descriptor": {
                            "Select": [
                                {"Value": "D0", "Name": "Disease"},
                                {"Value": "D1", "Name": "January"},
                                {"Value": "D2", "Name": "February"},
                            ]
                        },
                    }
                }
            }
        ]
    }

    timestamp, rows = decode_powerbi_dm0(response)

    assert timestamp == "2026-08-03T10:00:00Z"
    assert rows == [
        {"Disease": "Measles", "January": 2, "February": 5},
        {"Disease": "Measles", "January": None, "February": 7},
    ]


def test_normalize_powerbi_monthly_rows_preserves_zero_and_reconciles_ytd() -> None:
    visual = MonthlyVisual(
        model_id=3305775,
        dataset_id="dataset-ontario",
        query={},
        page_name="Monthly Data Table",
        visual_name=DEFAULT_VISUAL_NAME,
        title="Disease cases by month 2026",
        model_refresh_time="2026-08-02T07:00:00Z",
    )

    timestamp, rows = normalize_powerbi_monthly_rows(
        _powerbi_monthly_response(),
        visual=visual,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )

    assert timestamp == "2026-08-01T12:34:56Z"
    assert [(row["Month"], row["Cases"]) for row in rows] == [
        ("1", "2"),
        ("2", "0"),
        ("3", "3"),
    ]
    assert {row["YearToDateCases"] for row in rows} == {"5"}
    assert {row["GeographyKey"] for row in rows} == {ONTARIO_GEOGRAPHY_KEY}
    assert {row["DatasetStatus"] for row in rows} == {"preliminary"}
    assert {row["AcquisitionMode"] for row in rows} == {"powerbi_read_only"}
    assert {row["AuthoritativeRevision"] for row in rows} == {"false"}
    assert {row["AllowEqualQualityOverwrite"] for row in rows} == {"false"}


def test_normalize_powerbi_monthly_rows_rejects_ytd_mismatch() -> None:
    visual = MonthlyVisual(
        model_id=3305775,
        dataset_id="dataset-ontario",
        query={},
        page_name="Monthly Data Table",
        visual_name=DEFAULT_VISUAL_NAME,
        title="Disease cases by month 2026",
        model_refresh_time="",
    )

    with pytest.raises(ValueError, match=r"YTD mismatch.*months=5, ytd=6"):
        normalize_powerbi_monthly_rows(
            _powerbi_monthly_response(ytd_cases=6),
            visual=visual,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )


def test_official_csv_file_mode_distinguishes_zero_blank_and_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_csv = tmp_path / "pho_idto_2026.csv"
    with source_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Ontario Infectious Disease Trends 2026"])
        writer.writerow(
            [
                "Disease",
                "Jan",
                "Feb",
                "Mar",
                "YTD Total",
                "YTD rate per 100000 population",
            ]
        )
        writer.writerow(["Measles", "0", "", "<5", "<5", "0.03"])

    output_csv = tmp_path / "normalized.csv"
    monkeypatch.setenv("CA_ON_IDTO_FILE", str(source_csv))
    raw_dir = tmp_path / "raw"
    crawler = CanadaOntarioPHOCrawler(save_raw=True, raw_dir=raw_dir)

    def fail_live_fetch():
        raise AssertionError("official file mode must not call the live Power BI path")

    monkeypatch.setattr(crawler, "_fetch_live", fail_live_fetch)
    summary = crawler.crawl_monthly_ontario(
        output_csv, use_configured_file=True
    )

    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert summary.acquisition_mode == "official_export_file"
    assert summary.reporting_year == 2026
    assert summary.row_count == 2
    assert summary.disease_count == 1
    assert summary.latest_date is not None
    assert summary.latest_date.isoformat() == "2026-03-01"
    assert [(row["Month"], row["Cases"]) for row in rows] == [
        ("1", "0"),
        ("3", "<5"),
    ]
    assert all(row["YearToDateCases"] == "<5" for row in rows)
    assert all(row["GeographyKey"] == ONTARIO_GEOGRAPHY_KEY for row in rows)
    assert all(row["AcquisitionMode"] == "official_export_file" for row in rows)
    assert all(row["AuthoritativeRevision"] == "false" for row in rows)
    assert all(row["AllowEqualQualityOverwrite"] == "false" for row in rows)
    manifests = list(raw_dir.rglob("manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["tokens_persisted"] is False
    artifact = next(iter(manifest["artifacts"].values()))
    assert len(artifact["sha256"]) == 64
    assert artifact["bytes"] == source_csv.stat().st_size
    assert summary.source_artifact_sha256 == artifact["sha256"]
    assert summary.source_file_mtime
    assert manifest["original_mtime_utc"] == summary.source_file_mtime


@pytest.mark.parametrize(
    ("disease", "expected"),
    [
        (
            "Acquired immunodeficiency syndrome (AIDS)",
            "PHO AIDS Diagnosis Status Date",
        ),
        ("HIV", "PHO HIV Encounter Date (Reported Date)"),
        ("Tuberculosis", "PHO tuberculosis Diagnosis Date"),
        (
            "Carbapenemase-producing Enterobacteriaceae (CPE)",
            "PHO earliest specimen collection date",
        ),
    ],
)
def test_source_specific_time_basis_is_preserved(
    disease: str, expected: str
) -> None:
    visual = MonthlyVisual(
        model_id=3305775,
        dataset_id="dataset-ontario",
        query={},
        page_name="Monthly Data Table",
        visual_name=DEFAULT_VISUAL_NAME,
        title="Disease cases by month 2026",
        model_refresh_time="",
    )

    _, rows = normalize_powerbi_monthly_rows(
        _powerbi_monthly_response(disease=disease),
        visual=visual,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )

    assert {row["TimeBasis"] for row in rows} == {expected}


def test_normalized_content_hash_ignores_operational_request_metadata() -> None:
    first = [
        {
            "Date": "2026-01-01",
            "Cases": "2",
            "RetrievedAt": "first",
            "DatasetTimestamp": "query-one",
            "AcquisitionMode": "powerbi_read_only",
            "AuthoritativeRevision": "true",
            "AllowEqualQualityOverwrite": "true",
        }
    ]
    replay = [
        {
            "Date": "2026-01-01",
            "Cases": "2",
            "RetrievedAt": "later",
            "DatasetTimestamp": "query-two",
            "AcquisitionMode": "official_export_file",
            "AuthoritativeRevision": "false",
            "AllowEqualQualityOverwrite": "false",
        }
    ]

    assert _content_hash(first) == _content_hash(replay)


def test_dm0_rejects_late_schema_and_out_of_width_mask() -> None:
    late_schema = _powerbi_monthly_response()
    dm0 = late_schema["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"]
    dm0.append(copy.deepcopy(dm0[0]))
    with pytest.raises(ValueError, match="schema must appear exactly once"):
        decode_powerbi_dm0(late_schema)

    wide_mask = _powerbi_monthly_response()
    encoded = wide_mask["results"][0]["result"]["data"]["dsr"]["DS"][0]["PH"][0]["DM0"][0]
    encoded["Ø"] = 1 << len(encoded["S"])
    with pytest.raises(ValueError, match="mask exceeds the schema width"):
        decode_powerbi_dm0(wide_mask)


def test_dm0_rejects_duplicate_semantic_descriptor() -> None:
    response = _powerbi_monthly_response()
    selections = response["results"][0]["result"]["data"]["descriptor"]["Select"]
    selections[1]["Name"] = selections[0]["Name"]

    with pytest.raises(ValueError, match="descriptor is ambiguous"):
        decode_powerbi_dm0(response)


def test_export_rejects_subprovincial_rows_and_ytd_mismatch() -> None:
    with pytest.raises(ValueError, match="sub-provincial"):
        normalize_export_table(
            ["Ontario monthly data 2026"],
            [
                {
                    "Disease": "Measles",
                    "Jan": "2",
                    "YTD Total": "2",
                    "Public Health Unit": "Toronto",
                }
            ],
            reporting_year=2026,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )

    with pytest.raises(ValueError, match="YTD mismatch"):
        normalize_export_table(
            ["Ontario monthly data 2026"],
            [{"Disease": "Measles", "Jan": "2", "Feb": "3", "YTD Total": "99"}],
            reporting_year=2026,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )


def test_long_export_uses_disease_name_and_parses_year_month() -> None:
    rows = normalize_export_table(
        [],
        [
            {
                "Disease Code": "MORB-1",
                "Disease Name": "Measles",
                "Month": "2026-01",
                "Cases": "7",
                "Year": "2026",
            }
        ],
        reporting_year=None,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )

    assert len(rows) == 1
    assert rows[0]["RawDiseaseLabel"] == "Measles"
    assert rows[0]["Date"] == "2026-01-01"


def test_export_requires_unique_year_and_preserves_existing_output_on_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "undated.csv"
    source.write_text("Disease,Jan,YTD Total\nMeasles,2,99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one reporting year"):
        normalize_export_file(source)

    output = tmp_path / "current.csv"
    output.write_text("sentinel\n", encoding="utf-8")
    dated = tmp_path / "pho_2026.csv"
    dated.write_text("Disease,Jan,YTD Total\nMeasles,2,99\n", encoding="utf-8")
    crawler = CanadaOntarioPHOCrawler(save_raw=False)
    with pytest.raises(ValueError, match="YTD mismatch"):
        crawler.crawl_monthly_ontario(output, input_file=dated)
    assert output.read_text(encoding="utf-8") == "sentinel\n"


def test_xlsx_with_multiple_eligible_tables_fails_closed(tmp_path: Path) -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "Monthly A"
    second = workbook.create_sheet("Monthly B")
    for sheet in (first, second):
        sheet.append(["Ontario monthly data 2026"])
        sheet.append(["Disease", "Jan", "YTD Total"])
        sheet.append(["Measles", 2, 2])
    path = tmp_path / "pho_2026.xlsx"
    workbook.save(path)

    with pytest.raises(ValueError, match="multiple eligible monthly tables"):
        normalize_export_file(path)


def test_export_row_limit_and_archive_token_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pho_2026.csv"
    path.write_text(
        "Disease,Jan,YTD Total\nMeasles,2,2\nMumps,1,1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ca_module, "MAX_EXPORT_ROWS", 2)
    with pytest.raises(ValueError, match="row limit"):
        normalize_export_file(path)

    with pytest.raises(ValueError, match="credential fields"):
        _assert_archive_safe(
            {"accessToken": "should-never-be-written"},
            forbidden_values=("another-token",),
        )


@pytest.mark.parametrize("header", ["Age 5", "Dose 2", "Quarter 1", "Rate 10"])
def test_export_does_not_treat_arbitrary_numeric_headers_as_months(
    header: str,
) -> None:
    assert _month_column(header) is None
    with pytest.raises(ValueError, match="unrecognized populated column"):
        normalize_export_table(
            ["Ontario monthly data 2026"],
            [{"Disease": "Measles", header: "7"}],
            reporting_year=2026,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01", (1, None)),
        ("1", (1, None)),
        ("2026-01", (1, 2026)),
        ("01-2026", (1, 2026)),
        (date(2026, 1, 1), (1, 2026)),
    ],
)
def test_month_parser_accepts_only_complete_month_shapes(
    value: object, expected: tuple[int, int | None]
) -> None:
    assert _month_column(value) == expected


def test_dm0_rejects_multiple_containers_and_reduced_results() -> None:
    multiple_results = _powerbi_monthly_response()
    multiple_results["results"].append(copy.deepcopy(multiple_results["results"][0]))
    with pytest.raises(ValueError, match="exactly one result"):
        decode_powerbi_dm0(multiple_results)

    multiple_datasets = _powerbi_monthly_response()
    datasets = multiple_datasets["results"][0]["result"]["data"]["dsr"]["DS"]
    datasets.append(copy.deepcopy(datasets[0]))
    with pytest.raises(ValueError, match="exactly one dataset"):
        decode_powerbi_dm0(multiple_datasets)

    incomplete = _powerbi_monthly_response()
    incomplete["results"][0]["result"]["data"]["dsr"]["DS"][0]["IC"] = False
    with pytest.raises(ValueError, match="incomplete reduced dataset"):
        decode_powerbi_dm0(incomplete)


def test_live_file_environment_override_requires_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_file = tmp_path / "pho_2026.csv"
    configured_file.write_text(
        "Disease,Jan,YTD Total\nMeasles,99,99\n", encoding="utf-8"
    )
    monkeypatch.setenv("CA_ON_IDTO_FILE", str(configured_file))
    crawler = CanadaOntarioPHOCrawler(save_raw=False)
    visual = MonthlyVisual(
        model_id=3305775,
        dataset_id="dataset-ontario",
        query={},
        page_name="Monthly Data Table",
        visual_name=DEFAULT_VISUAL_NAME,
        title="Disease cases by month 2026",
        model_refresh_time="2026-07-16T15:28:46.083",
    )
    monkeypatch.setattr(
        crawler,
        "_fetch_live",
        lambda: (
            visual,
            "https://wabi-canada-central-api.analysis.windows.net",
            {},
            _powerbi_monthly_response(),
            "short-lived-token",
        ),
    )

    summary = crawler.crawl_monthly_ontario(tmp_path / "normalized.csv")

    assert summary.acquisition_mode == "powerbi_read_only"
    assert summary.row_count == 3


def _live_release_rows(refresh_time: str) -> list[dict[str, str]]:
    visual = MonthlyVisual(
        model_id=3305775,
        dataset_id="dataset-ontario",
        query={},
        page_name="Monthly Data Table",
        visual_name=DEFAULT_VISUAL_NAME,
        title="Disease cases by month 2026",
        model_refresh_time=refresh_time,
    )
    return normalize_powerbi_monthly_rows(
        _powerbi_monthly_response(),
        visual=visual,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )[1]


def test_live_release_policy_is_idempotent_and_prevents_rollback() -> None:
    initial = _live_release_rows("2026-07-16T15:28:46.083")
    assert CAOntarioMonthlyUpdater.authorize_release_for_persistence(
        initial, stored_model_refresh_time=None
    ) == "initial_live_release"
    assert {row["AuthoritativeRevision"] for row in initial} == {"true"}

    unchanged = _live_release_rows("2026-07-16T15:28:46.083")
    assert CAOntarioMonthlyUpdater.authorize_release_for_persistence(
        unchanged,
        stored_model_refresh_time="2026-07-16T15:28:46.083",
    ) == "unchanged_live_release"
    assert {row["AllowEqualQualityOverwrite"] for row in unchanged} == {"false"}

    newer = _live_release_rows("2026-08-16T15:28:46.083")
    assert CAOntarioMonthlyUpdater.authorize_release_for_persistence(
        newer,
        stored_model_refresh_time="2026-07-16T15:28:46.083",
    ) == "newer_live_release"
    assert {row["AllowEqualQualityOverwrite"] for row in newer} == {"true"}

    stale = _live_release_rows("2026-06-16T15:28:46.083")
    with pytest.raises(ValueError, match="older than the newest stored release"):
        CAOntarioMonthlyUpdater.authorize_release_for_persistence(
            stale,
            stored_model_refresh_time="2026-07-16T15:28:46.083",
        )


def test_reviewed_source_labels_partition_registered_and_excluded_diseases() -> None:
    registered, excluded, reviewed = (
        CAOntarioMonthlyUpdater._load_source_label_contract()
    )

    assert len(registered) == 54
    assert len(excluded) == 0
    assert len(reviewed) == 54
    assert registered.isdisjoint(excluded)
    assert registered | excluded == reviewed
    assert "Mpox" in registered
    assert "Mpox" not in excluded
    assert "Carbapenemase-producing Enterobacteriaceae (CPE)" in registered
    assert "Syphilis, Infectious" in registered


def test_source_label_contract_rejects_unknown_and_incomplete_live_manifests() -> None:
    with pytest.raises(ValueError, match="unreviewed disease labels: New Disease"):
        CAOntarioMonthlyUpdater._validate_source_label_contract(
            [
                {
                    "RawDiseaseLabel": "New Disease",
                    "AcquisitionMode": "official_export_file",
                }
            ]
        )

    with pytest.raises(ValueError, match="live disease-label manifest changed"):
        CAOntarioMonthlyUpdater._validate_source_label_contract(
            [
                {
                    "RawDiseaseLabel": "Measles",
                    "AcquisitionMode": "powerbi_read_only",
                }
            ]
        )


def test_export_rejects_mixed_or_duplicate_month_schemas() -> None:
    with pytest.raises(ValueError, match="mixes wide and long"):
        normalize_export_table(
            ["Ontario monthly data 2026"],
            [
                {
                    "Disease": "Measles",
                    "Month": "2026-02",
                    "Cases": "9",
                    "Jan": "2",
                }
            ],
            reporting_year=2026,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )

    with pytest.raises(ValueError, match="duplicate month columns"):
        normalize_export_table(
            ["Ontario monthly data 2026"],
            [{"Disease": "Measles", "Jan": "2", "January": "2"}],
            reporting_year=2026,
            retrieved_at="2026-08-03T11:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_live_snapshot_continuity_rejects_blank_retractions() -> None:
    rows = _live_release_rows("2026-07-16T15:28:46.083")
    measles_series = CAOntarioMonthlyUpdater._registered_series_by_label()["measles"]

    class _Result:
        def __init__(self, identities: list[tuple[date, str]]) -> None:
            self.identities = identities

        def fetchall(self) -> list[tuple[date, str]]:
            return self.identities

    class _Database:
        def __init__(self, identities: list[tuple[date, str]]) -> None:
            self.identities = identities
            self.params: dict[str, object] = {}

        async def execute(
            self, _statement: object, _params: dict[str, object]
        ) -> _Result:
            self.params = _params
            return _Result(self.identities)

    updater = CAOntarioMonthlyUpdater()
    existing_db = _Database([(date(2026, 1, 1), measles_series)])
    assert await updater.validate_live_snapshot_continuity(
        existing_db, rows
    ) == 1
    assert existing_db.params["year_start"].tzinfo is timezone.utc
    assert existing_db.params["year_end"].tzinfo is timezone.utc

    with pytest.raises(ValueError, match="retracts stored observations"):
        await updater.validate_live_snapshot_continuity(
            _Database([(date(2026, 4, 1), measles_series)]), rows
        )


@pytest.mark.asyncio
async def test_ontario_legacy_projection_uses_independent_jurisdiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = normalize_export_table(
        [],
        [{"Disease": "Measles", "Month": "2026-01", "Cases": "7"}],
        reporting_year=2026,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )
    updater = CAOntarioMonthlyUpdater()

    async def no_retractions(_db: object, _rows: object) -> int:
        return 0

    async def no_stored_release(_db: object) -> None:
        return None

    async def country_id(_db: object) -> int:
        return 77

    async def mapping(_db: object) -> dict[str, int]:
        return {"measles": 17}

    monkeypatch.setattr(updater, "validate_live_snapshot_continuity", no_retractions)
    monkeypatch.setattr(updater, "get_db_model_refresh_time", no_stored_release)
    monkeypatch.setattr(updater, "_get_country_id", country_id)
    monkeypatch.setattr(updater, "_load_mapping_dict", mapping)

    class _Database:
        def __init__(self) -> None:
            self.params: list[dict[str, object]] = []

        async def execute(
            self, _statement: object, params: list[dict[str, object]]
        ) -> None:
            self.params.extend(params)

    db = _Database()
    result = await updater.import_rows(
        db,
        rows,
        db_latest_date=None,
        source_latest_date=date(2026, 1, 1),
    )

    assert updater.country_code == "CA-ON"
    assert updater.series_geography_key == "country:CA-ON:national"
    assert result.inserted_or_updated == 1
    assert db.params[0]["country_id"] == 77
    assert db.params[0]["region"] == "Ontario"
    assert db.params[0]["cases"] == 7
    metadata = json.loads(str(db.params[0]["metadata"]))
    assert metadata["parent_country_code"] == "CA"
    assert metadata["location_type"] == "subdivision"


@pytest.mark.asyncio
async def test_ontario_suppressed_value_is_not_coerced_into_legacy_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = normalize_export_table(
        [],
        [{"Disease": "Measles", "Month": "2026-01", "Cases": "<5"}],
        reporting_year=2026,
        retrieved_at="2026-08-03T11:00:00+00:00",
    )
    updater = CAOntarioMonthlyUpdater()

    async def no_retractions(_db: object, _rows: object) -> int:
        return 0

    async def no_stored_release(_db: object) -> None:
        return None

    async def country_id(_db: object) -> int:
        return 77

    async def mapping(_db: object) -> dict[str, int]:
        return {"measles": 17}

    monkeypatch.setattr(updater, "validate_live_snapshot_continuity", no_retractions)
    monkeypatch.setattr(updater, "get_db_model_refresh_time", no_stored_release)
    monkeypatch.setattr(updater, "_get_country_id", country_id)
    monkeypatch.setattr(updater, "_load_mapping_dict", mapping)

    class _NoWriteDatabase:
        async def execute(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("suppressed facts must stay in the lossless series layer")

    result = await updater.import_rows(
        _NoWriteDatabase(),
        rows,
        db_latest_date=None,
        source_latest_date=date(2026, 1, 1),
    )
    assert result.inserted_or_updated == 0


def test_ontario_connector_does_not_replace_canada_national_defaults() -> None:
    assert get_country_bootstrap_config("CA").get("data_source_url") is None
    assert get_expected_scopes_for_country("CA") == ["all"]
    assert default_source_for_country("CA") == "all"
    assert source_scope_label("all", country_code="CA") == "All Sources"
    assert get_expected_scopes_for_country("CA-ON") == ["pho_idto_monthly"]
    assert default_source_for_country("CA-ON") == "pho_idto_monthly"
    assert source_scope_label(
        "pho_idto_monthly", country_code="CA-ON"
    ) == "Public Health Ontario IDTO Monthly"
