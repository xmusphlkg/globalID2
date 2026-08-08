from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from src.data.storage.series_observation_store import (
    SeriesObservationQualityError,
    SeriesObservationQualityPolicy,
    SeriesObservationQuarantinedError,
    SeriesObservationStore,
    _quality_status,
)


@pytest.fixture(autouse=True)
def _stub_shared_mutation_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_db) -> None:
        return None

    monkeypatch.setattr(
        "src.data.storage.series_observation_store."
        "acquire_disease_data_mutation_lock",
        _noop,
    )


def _quality_observation(
    series_code: str,
    report_time: str,
    value: float,
    *,
    frequency: str = "monthly",
) -> dict:
    return {
        "time": datetime.fromisoformat(report_time).replace(tzinfo=timezone.utc),
        "series_code": series_code,
        "geography_key": "country:ZZ:national",
        "dimension_key": "all",
        "value": value,
        "unit": "count",
        "suppressed": False,
        "metadata": {"frequency": frequency},
    }


def test_quality_guard_keeps_an_isolated_reported_zero() -> None:
    report = SeriesObservationStore.assess_quality(
        [_quality_observation("SER_ONE", "2026-01-01", 0)],
        country_code="ZZ",
    )

    assert not any("zero" in issue.code for issue in report.issues)


def test_quality_guard_quarantines_cross_series_all_zero_tail() -> None:
    observations = [
        _quality_observation(f"SER_{series}", f"2026-0{month}-01", 0)
        for month in range(1, 4)
        for series in range(5)
    ]
    history = [
        _quality_observation(f"SER_{series}", "2025-12-01", series + 1)
        for series in range(5)
    ]
    policy = SeriesObservationQualityPolicy(mode="quarantine")
    report = SeriesObservationStore.assess_quality(
        observations,
        country_code="ZZ",
        existing_observations=history,
        policy=policy,
    )

    issue = next(
        issue for issue in report.issues if issue.code == "cross_series_all_zero_tail"
    )
    assert issue.severity == "critical"
    assert issue.details["tail_period_count"] == 3
    assert issue.details["prior_stage"] == "stored_history"
    with pytest.raises(SeriesObservationQuarantinedError) as exc:
        SeriesObservationStore.enforce_quality(report, policy)
    assert exc.value.report is report


def test_quality_guard_fail_closed_rejects_short_zero_tail_but_report_mode_allows_it() -> (
    None
):
    observations = [
        _quality_observation(f"SER_{series}", f"2026-0{month}-01", 0)
        for month in range(1, 3)
        for series in range(5)
    ]
    fail_policy = SeriesObservationQualityPolicy(mode="fail_closed")
    report = SeriesObservationStore.assess_quality(
        observations,
        country_code="ZZ",
        policy=fail_policy,
    )

    assert any(
        issue.code == "cross_series_all_zero_tail_short" and issue.severity == "error"
        for issue in report.issues
    )
    with pytest.raises(SeriesObservationQualityError):
        SeriesObservationStore.enforce_quality(report, fail_policy)
    SeriesObservationStore.enforce_quality(
        report, SeriesObservationQualityPolicy(mode="report")
    )


def test_quality_guard_reports_batch_and_projected_series_gaps() -> None:
    observations = [
        _quality_observation("SER_GAPPED", "2026-01-01", 2),
        _quality_observation("SER_GAPPED", "2026-03-01", 3),
    ]
    report = SeriesObservationStore.assess_quality(
        observations,
        country_code="ZZ",
    )

    by_code = {issue.code: issue for issue in report.issues}
    assert by_code["incoming_batch_period_gap"].stage == "pre_save"
    projected = by_code["projected_series_time_gap"]
    assert projected.stage == "projected_post_save"
    assert projected.details["missing_period_count"] == 1
    assert projected.details["gap_examples"][0]["time"].startswith("2026-02-01")


def test_required_registry_coverage_fails_closed_for_one_unmatched_row() -> None:
    policy = SeriesObservationQualityPolicy(
        mode="quarantine", registry_coverage="required"
    )
    report = SeriesObservationStore.assess_quality(
        [],
        country_code="ZZ",
        source_row_count=1,
        skipped_unmatched=1,
        policy=policy,
    )

    issue = next(
        item
        for item in report.issues
        if item.code == "required_registry_coverage_incomplete"
    )
    assert issue.severity == "critical"
    assert issue.details["skipped_unmatched"] == 1
    with pytest.raises(SeriesObservationQuarantinedError):
        SeriesObservationStore.enforce_quality(report, policy)


def test_required_registry_coverage_fails_closed_for_one_invalid_row() -> None:
    policy = SeriesObservationQualityPolicy(
        mode="quarantine", registry_coverage="required"
    )
    report = SeriesObservationStore.assess_quality(
        [_quality_observation("SER_VALID", "2026-01-01", 2)],
        country_code="ZZ",
        source_row_count=2,
        skipped_invalid=1,
        policy=policy,
    )

    issue = next(
        item
        for item in report.issues
        if item.code == "required_registry_coverage_incomplete"
    )
    assert issue.severity == "critical"
    assert issue.details["skipped_invalid"] == 1
    with pytest.raises(SeriesObservationQuarantinedError):
        SeriesObservationStore.enforce_quality(report, policy)


def test_legacy_only_source_is_explicitly_exempt_from_registry_coverage() -> None:
    policy = SeriesObservationQualityPolicy(
        mode="quarantine", registry_coverage="legacy_only"
    )
    report = SeriesObservationStore.assess_quality(
        [],
        country_code="ZZ",
        source_row_count=1,
        skipped_unmatched=1,
        policy=policy,
    )

    assert report.issues == ()
    SeriesObservationStore.enforce_quality(report, policy)


def test_single_series_destructive_revision_is_quarantined_unless_authoritative() -> None:
    history = [_quality_observation("SER_ONE", "2026-01-01", 100)]
    incoming = [_quality_observation("SER_ONE", "2026-01-01", 0)]
    policy = SeriesObservationQualityPolicy(mode="quarantine")
    report = SeriesObservationStore.assess_quality(
        incoming,
        country_code="ZZ",
        existing_observations=history,
        policy=policy,
    )

    assert any(
        item.code == "single_series_positive_overwritten_by_zero"
        and item.severity == "critical"
        for item in report.issues
    )
    with pytest.raises(SeriesObservationQuarantinedError):
        SeriesObservationStore.enforce_quality(report, policy)

    incoming[0]["metadata"]["authoritative_revision"] = True
    authoritative = SeriesObservationStore.assess_quality(
        incoming,
        country_code="ZZ",
        existing_observations=history,
        policy=policy,
    )
    assert not any(
        "revision" in item.code or "overwritten" in item.code
        for item in authoritative.issues
    )


def test_us_case_status_components_keep_distinct_series_identities() -> None:
    store = SeriesObservationStore()
    rows = [
        {
            "Date": "2026-07-04",
            "DiseaseCode": "10105",
            "RawDiseaseLabel": "Hepatitis B, chronic, Confirmed",
            "ReportingArea": "US RESIDENTS",
            "Cases": "4",
        },
        {
            "Date": "2026-07-04",
            "DiseaseCode": "10105",
            "RawDiseaseLabel": "Hepatitis B, chronic, Probable",
            "ReportingArea": "US RESIDENTS",
            "Cases": "2",
        },
    ]

    result = store.build_observations(rows, "US", source_id="SRC_US_NNDSS")

    assert result.skipped_ambiguous == 0
    assert {row["series_code"] for row in result.observations} == {
        "SER_US_CHRONIC_HEPATITIS_B_CONFIRMED",
        "SER_US_CHRONIC_HEPATITIS_B_PROBABLE",
    }
    assert {
        (row["time"], row["series_code"], row["geography_key"], row["dimension_key"])
        for row in result.observations
    } == {
        (
            row["time"],
            row["series_code"],
            "country:US:national",
            "all",
        )
        for row in result.observations
    }


def test_code_without_case_status_is_rejected_as_ambiguous() -> None:
    result = SeriesObservationStore().build_observations(
        [{"Date": "2026-07-04", "DiseaseCode": "10105", "Cases": "4"}],
        "US",
        source_id="SRC_US_NNDSS",
    )

    assert result.observations == []
    assert result.skipped_ambiguous == 1


def test_au_acquisition_series_and_suppression_are_preserved() -> None:
    rows = [
        {
            "Date": "2026-06-01",
            "RawDiseaseLabel": "Hepatitis C (newly acquired)",
            "Cases": "12",
            "DatasetStatus": "preliminary",
        },
        {
            "Date": "2026-06-01",
            "RawDiseaseLabel": "Hepatitis C (unspecified)",
            "Cases": "*",
        },
    ]

    result = SeriesObservationStore().build_observations(
        rows, "AU", source_id="SRC_AU_NINDSS"
    )

    assert [row["series_code"] for row in result.observations] == [
        "SER_AU_HEPATITIS_C_NEW",
        "SER_AU_HEPATITIS_C_UNSPECIFIED",
    ]
    assert result.observations[0]["quality_status"] == "provisional"
    assert result.observations[1]["suppressed"] is True
    assert result.observations[1]["value"] is None


def test_numeric_threshold_suppression_is_not_treated_as_invalid() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-06-01",
                "RawDiseaseLabel": "Hepatitis C (unspecified)",
                "Cases": "<10",
            }
        ],
        "AU",
        source_id="SRC_AU_NINDSS",
    )

    assert result.skipped_invalid == 0
    assert len(result.observations) == 1
    assert result.observations[0]["suppressed"] is True
    assert result.observations[0]["value"] is None
    assert result.observations[0]["raw_data"]["Cases"] == "<10"


def test_revised_quality_outranks_provisional_marker() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-06-01",
                "RawDiseaseLabel": "Hepatitis C (newly acquired)",
                "Cases": "12",
                "DatasetStatus": "revised preliminary release",
            }
        ],
        "AU",
        source_id="SRC_AU_NINDSS",
    )

    assert result.observations[0]["quality_status"] == "revised"
    assert result.observations[0]["metadata"]["authoritative_revision"] is True


def test_source_data_status_marks_open_month_provisional() -> None:
    assert _quality_status({"DataStatus": "provisional"}) == "provisional"


def test_cn_code_and_local_label_resolve_reported_aggregate_and_subtype() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2023-08-01",
                "Diseases": "Hepatitis",
                "DiseasesCN": "病毒性肝炎",
                "Cases": "100",
            },
            {
                "Date": "2023-08-01",
                "Diseases": "Hepatitis B",
                "DiseasesCN": "乙型肝炎",
                "Cases": "60",
            },
            {
                "Date": "2023-08-01",
                "Diseases": "Other hepatitis",
                "DiseasesCN": "肝炎（未分型）",
                "Cases": "4",
            },
        ],
        "CN",
        source_id="SRC_CN_CDC",
    )

    assert [item["series_code"] for item in result.observations] == [
        "SER_CN_VIRAL_HEPATITIS",
        "SER_CN_HEPATITIS_B_UNSPECIFIED_COURSE",
        "SER_CN_UNSPECIFIED_VIRAL_HEPATITIS",
    ]


def test_jp_week_fields_generate_utc_week_end_and_national_geography() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "MMWRYear": "2025",
                "MMWRWeek": "1",
                "ReportingArea": "総数",
                "RawDiseaseLabel": "AIDS",
                "Current week": "2",
            },
            {
                "Current MMWR Year": "2025",
                "MMWR WEEK": "2",
                "Reporting Area": "全国",
                "Disease": "AIDS",
                "Current week": "3",
            },
        ],
        "JP",
        source_id="SRC_JP_NIID",
        value_field="Current week",
    )

    assert result.skipped_invalid == 0
    assert [row["time"] for row in result.observations] == [
        datetime(2025, 1, 5, tzinfo=timezone.utc),
        datetime(2025, 1, 12, tzinfo=timezone.utc),
    ]
    assert {row["series_code"] for row in result.observations} == {"SER_JP_AIDS_WEEKLY"}
    assert {row["geography_key"] for row in result.observations} == {
        "country:JP:national"
    }
    assert [row["value"] for row in result.observations] == [2.0, 3.0]


def test_jp_iso_week_year_boundary_does_not_collapse_week_53_and_week_1() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Current MMWR Year": "2015",
                "MMWR WEEK": "53",
                "Disease": "AIDS",
                "Current week": "18",
            },
            {
                "Current MMWR Year": "2016",
                "MMWR WEEK": "1",
                "Disease": "AIDS",
                "Current week": "11",
            },
        ],
        "JP",
        source_id="SRC_JP_NIID",
        value_field="Current week",
        geography_key="country:JP:national",
    )

    assert result.skipped_invalid == 0
    assert [row["time"] for row in result.observations] == [
        datetime(2016, 1, 3, tzinfo=timezone.utc),
        datetime(2016, 1, 10, tzinfo=timezone.utc),
    ]


def test_us_hiv_source_absence_does_not_become_a_zero_observation() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-06-01",
                "RawDiseaseLabel": "HIV infection",
                "Cases": "0",
            }
        ],
        "US",
        source_id="SRC_US_NNDSS",
    )

    assert result.observations == []
    assert result.skipped_unmatched == 1


def test_historical_aids_series_is_bounded_and_numeric_zero_is_retained() -> None:
    store = SeriesObservationStore()
    in_range = store.build_observations(
        [
            {
                "Date": "2022-12-31",
                "RawDiseaseLabel": "AIDS classifications",
                "Cases": 0,
            }
        ],
        "US",
        source_id="SRC_US_NHSS",
    )
    out_of_range = store.build_observations(
        [
            {
                "Date": "2023-12-31",
                "RawDiseaseLabel": "AIDS classifications",
                "Cases": 1,
            }
        ],
        "US",
        source_id="SRC_US_NHSS",
    )

    assert in_range.observations[0]["value"] == 0.0
    assert out_of_range.observations == []
    assert out_of_range.skipped_unmatched == 1


def test_dimensions_use_bounded_hash_and_source_geography() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-07-04",
                "RawDiseaseLabel": "Hepatitis C (newly acquired)",
                "Cases": "2",
                "ReportingArea": "Queensland",
                "Geocode": "QLD",
                "Dimensions": '{"sex":"female","age":"20-29"}',
            }
        ],
        "AU",
        source_id="SRC_AU_NINDSS",
    )

    observation = result.observations[0]
    assert observation["geography_key"] == "country:AU:source-area:QLD"
    assert observation["dimension_key"].startswith("sha256:")
    assert len(observation["dimension_key"]) == 71
    assert observation["dimensions"] == {"sex": "female", "age": "20-29"}


@pytest.mark.parametrize("reporting_area", ["US RESIDENTS", "U.S. Residents"])
def test_us_resident_aliases_are_resolved_as_national_for_nndss(
    reporting_area: str,
) -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-07-04",
                "Source": "US CDC NNDSS",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "ReportingArea": reporting_area,
                "Cases": "2",
            }
        ],
        "US",
        source_id={
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
    )

    assert result.observations[0]["geography_key"] == "country:US:national"


def test_nndss_total_and_us_residents_have_distinct_natural_keys() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2026-07-04",
                "Source": "US CDC NNDSS",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "ReportingArea": "TOTAL",
                "PopulationScope": (
                    "nndss_total_including_us_residents_territories_"
                    "and_non_us_residents"
                ),
                "Cases": "3",
            },
            {
                "Date": "2026-07-04",
                "Source": "US CDC NNDSS",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "ReportingArea": "US RESIDENTS",
                "PopulationScope": "us_residents_excluding_territories",
                "Cases": "2",
            },
        ],
        "US",
        source_id={
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
    )

    assert len(result.observations) == 2
    assert {
        (observation["geography_key"], observation["value"])
        for observation in result.observations
    } == {
        ("source:SRC_US_NNDSS:reporting-area:total", 3.0),
        ("country:US:national", 2.0),
    }
    assert {
        observation["metadata"]["population_scope"]
        for observation in result.observations
    } == {
        "nndss_total_including_us_residents_territories_and_non_us_residents",
        "us_residents_excluding_territories",
    }


def test_registry_row_selection_omits_unregistered_and_blank_but_keeps_invalid() -> None:
    rows = [
        {
            "Date": "2026-07-04",
            "RawDiseaseLabel": "Hepatitis A, Confirmed",
            "ReportingArea": "US RESIDENTS",
            "Cases": "2",
        },
        {
            "Date": "2026-07-04",
            "RawDiseaseLabel": "Hepatitis A, Confirmed",
            "ReportingArea": "TOTAL",
            "Cases": "",
        },
        {
            "Date": "2026-07-04",
            "RawDiseaseLabel": "Hepatitis A, Confirmed",
            "ReportingArea": "TOTAL",
            "Cases": "not-a-number",
        },
        {
            "Date": "2026-07-04",
            "RawDiseaseLabel": "An unregistered NNDSS condition",
            "ReportingArea": "US RESIDENTS",
            "Cases": "7",
        },
    ]

    selected = SeriesObservationStore().select_registry_rows(
        rows,
        "US",
        source_id="SRC_US_NNDSS",
    )

    assert selected.rows == [rows[0], rows[2]]
    assert selected.skipped_missing == 1
    assert selected.skipped_unregistered == 1
    built = SeriesObservationStore().build_observations(
        selected.rows, "US", source_id="SRC_US_NNDSS"
    )
    assert built.skipped_invalid == 1


def test_unknown_nndss_reporting_area_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported SRC_US_NNDSS ReportingArea"):
        SeriesObservationStore().build_observations(
            [
                {
                    "Date": "2026-07-04",
                    "RawDiseaseLabel": "Hepatitis A, Confirmed",
                    "ReportingArea": "UNKNOWN AGGREGATE",
                    "Cases": "2",
                }
            ],
            "US",
            source_id="SRC_US_NNDSS",
        )


@pytest.mark.parametrize(
    ("reporting_area", "conflicting_key"),
    [
        ("TOTAL", "country:US:national"),
        ("U.S. Residents", "source:SRC_US_NNDSS:reporting-area:total"),
    ],
)
def test_explicit_geography_cannot_override_nndss_source_scope(
    reporting_area: str,
    conflicting_key: str,
) -> None:
    with pytest.raises(ValueError, match="Explicit geography_key conflicts"):
        SeriesObservationStore().build_observations(
            [
                {
                    "Date": "2026-07-04",
                    "RawDiseaseLabel": "Hepatitis A, Confirmed",
                    "ReportingArea": reporting_area,
                    "GeographyKey": conflicting_key,
                    "Cases": "2",
                }
            ],
            "US",
            source_id="SRC_US_NNDSS",
        )


def test_batch_geography_cannot_override_nndss_source_scope() -> None:
    with pytest.raises(ValueError, match="Batch geography_key conflicts"):
        SeriesObservationStore().build_observations(
            [
                {
                    "Date": "2026-07-04",
                    "RawDiseaseLabel": "Hepatitis A, Confirmed",
                    "ReportingArea": "TOTAL",
                    "Cases": "2",
                }
            ],
            "US",
            source_id="SRC_US_NNDSS",
            geography_key="country:US:national",
        )


def test_nhss_total_remains_us_national() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2024-12-31",
                "Source": "US CDC NHSS",
                "RawDiseaseLabel": (
                    "HIV diagnoses among persons aged 13 years and older"
                ),
                "ReportingArea": "TOTAL",
                "Cases": "38434",
            }
        ],
        "US",
        source_id={
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
    )

    assert result.observations[0]["geography_key"] == "country:US:national"


def test_conflicting_duplicate_natural_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="Conflicting source rows"):
        SeriesObservationStore().build_observations(
            [
                {
                    "Date": "2026-07-04",
                    "RawDiseaseLabel": "Hepatitis A, Confirmed",
                    "ReportingArea": "US RESIDENTS",
                    "Cases": "2",
                },
                {
                    "Date": "2026-07-04",
                    "RawDiseaseLabel": "Hepatitis A, Confirmed",
                    "ReportingArea": "US RESIDENTS",
                    "Cases": "3",
                },
            ],
            "US",
            source_id="SRC_US_NNDSS",
        )


def test_same_value_series_aliases_share_one_observation() -> None:
    result = SeriesObservationStore().build_observations(
        [
            {
                "Date": "2023-11-11",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "ReportingArea": "US RESIDENTS",
                "Cases": "15",
                "SortOrder": "20234503081",
            },
            {
                "Date": "2023-11-11",
                "RawDiseaseLabel": "Hepatitis, A, acute",
                "ReportingArea": "US RESIDENTS",
                "Cases": "15",
                "SortOrder": "20234503710",
            },
        ],
        "US",
        source_id="SRC_US_NNDSS",
    )

    assert len(result.observations) == 1
    assert result.observations[0]["value"] == 15
    assert result.observations[0]["geography_key"] == "country:US:national"


def test_non_finite_values_are_not_written_to_numeric_or_json_fields() -> None:
    store = SeriesObservationStore()
    invalid_value = store.build_observations(
        [
            {
                "Date": "2026-07-04",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "Cases": "NaN",
            }
        ],
        "US",
        source_id="SRC_US_NNDSS",
    )
    valid_value = store.build_observations(
        [
            {
                "Date": "2026-07-04",
                "RawDiseaseLabel": "Hepatitis A, Confirmed",
                "ReportingArea": "US RESIDENTS",
                "Cases": "2",
                "Deaths": float("nan"),
            }
        ],
        "US",
        source_id="SRC_US_NNDSS",
    )

    assert invalid_value.observations == []
    assert invalid_value.skipped_invalid == 1
    assert valid_value.observations[0]["raw_data"]["Deaths"] is None


@pytest.mark.asyncio
async def test_save_rows_builds_metadata_safe_postgresql_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ScalarResult:
        @staticmethod
        def scalars():
            return ["SER_US_NHSS_HIV_ANNUAL"]

        class _Mappings:
            @staticmethod
            def all():
                return []

        @staticmethod
        def mappings():
            return ScalarResult._Mappings()

    class FakeDB:
        def __init__(self) -> None:
            self.upserts = []

        async def execute(self, statement):
            if getattr(statement, "is_select", False):
                return ScalarResult()
            # Compilation catches the DeclarativeBase.metadata collision that
            # previously made this path fail before reaching PostgreSQL.
            statement.compile(dialect=postgresql.dialect())
            self.upserts.append(statement)

            class WriteResult:
                @staticmethod
                def scalars():
                    class Values:
                        @staticmethod
                        def all():
                            return ["SER_US_NHSS_HIV_ANNUAL"]

                    return Values()

            return WriteResult()

    db = FakeDB()
    locked_sessions = []

    async def acquire_lock(session) -> None:
        locked_sessions.append(session)

    monkeypatch.setattr(
        "src.data.storage.series_observation_store."
        "acquire_disease_data_mutation_lock",
        acquire_lock,
    )
    result = await SeriesObservationStore().save_rows(
        db,
        [
            {
                "Date": "2024-12-31",
                "RawDiseaseLabel": (
                    "HIV diagnoses among persons aged 13 years and older"
                ),
                "Cases": "38434",
            }
        ],
        "US",
        source_id="SRC_US_NHSS",
    )

    assert result.upserted == 1
    assert locked_sessions == [db]
    assert len(db.upserts) == 1
    compiled = str(db.upserts[0].compile(dialect=postgresql.dialect()))
    assert "DO UPDATE" in compiled
    assert "WHERE" in compiled
    assert "quality_status" in compiled


@pytest.mark.asyncio
async def test_save_rows_reports_only_rows_postgres_actually_affected() -> None:
    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def __iter__(self):
            return iter(self.values)

        def all(self):
            return self.values

        def mappings(self):
            return Result([])

    class FakeDB:
        async def execute(self, statement):
            if getattr(statement, "is_select", False):
                return Result(["SER_US_NHSS_HIV_ANNUAL"])
            # Simulate ON CONFLICT ... DO UPDATE WHERE rejecting a lower-quality
            # incoming row: PostgreSQL RETURNING contains no affected row.
            return Result([])

    result = await SeriesObservationStore().save_rows(
        FakeDB(),
        [
            {
                "Date": "2024-12-31",
                "RawDiseaseLabel": (
                    "HIV diagnoses among persons aged 13 years and older"
                ),
                "Cases": "38434",
            }
        ],
        "US",
        source_id="SRC_US_NHSS",
        quality_policy=SeriesObservationQualityPolicy(mode="off"),
    )

    assert result.upserted == 0
