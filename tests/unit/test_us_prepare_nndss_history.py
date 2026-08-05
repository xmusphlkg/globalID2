from __future__ import annotations

import pytest

from scripts import us_prepare_nndss_history as prepare


@pytest.mark.parametrize(
    "value",
    ["US RESIDENTS", "U.S. Residents", " united  states residents "],
)
def test_resident_reporting_area_aliases_are_canonicalized(value: str) -> None:
    assert (
        prepare.canonical_resident_reporting_area(value)
        == prepare.NNDSS_RESIDENT_REPORTING_AREA
    )


def test_total_is_not_a_legacy_us_national_reporting_area() -> None:
    assert prepare.canonical_resident_reporting_area("Total") is None
    with pytest.raises(ValueError, match="only US-resident NNDSS rows"):
        prepare.build_normalized_record(
            {
                "Reporting Area": "Total",
                "Current MMWR Year": "2025",
                "MMWR WEEK": "1",
                "Label": "Hepatitis A, Confirmed",
                "Current week": "2",
            },
            reporting_area_filter="TOTAL",
            source_name=prepare.DEFAULT_SOURCE_NAME,
            update_mode="test",
            source_file="fixture.csv",
        )


def test_current_resident_alias_projects_to_canonical_scope_without_false_zero() -> (
    None
):
    row = prepare.build_normalized_record(
        {
            "Reporting Area": "U.S. Residents",
            "Current MMWR Year": "2025",
            "MMWR WEEK": "1",
            "Label": "Hepatitis A, Confirmed",
            "Current week": "",
            "Current week, flag": "-",
        },
        reporting_area_filter="US RESIDENTS",
        source_name=prepare.DEFAULT_SOURCE_NAME,
        update_mode="test",
        source_file="fixture.csv",
    )

    assert row is not None
    assert row["ReportingArea"] == "US RESIDENTS"
    assert row["PopulationScope"] == "us_residents_excluding_territories"
    assert row["Cases"] == ""
    assert (
        prepare.normalize_rows(
            [
                {
                    "Reporting Area": "U.S. Residents",
                    "Current MMWR Year": "2025",
                    "MMWR WEEK": "1",
                    "Label": "Hepatitis A, Confirmed",
                    "Current week": "",
                    "Current week, flag": "-",
                }
            ],
            reporting_area="US RESIDENTS",
            source_name=prepare.DEFAULT_SOURCE_NAME,
            update_mode="test",
            source_file="fixture.csv",
        )
        == []
    )


def test_normalize_rows_prefers_current_resident_alias_on_overlap() -> None:
    base = {
        "Current MMWR Year": "2025",
        "MMWR WEEK": "1",
        "Label": "Hepatitis A, Confirmed",
        "Current week, flag": "",
    }
    rows = prepare.normalize_rows(
        [
            {**base, "Reporting Area": "US RESIDENTS", "Current week": "2"},
            {**base, "Reporting Area": "U.S. Residents", "Current week": "3"},
        ],
        reporting_area="US RESIDENTS",
        source_name=prepare.DEFAULT_SOURCE_NAME,
        update_mode="test",
        source_file="fixture.csv",
    )

    assert len(rows) == 1
    assert rows[0]["Cases"] == "3"


def test_normalize_rows_blocks_conflicts_within_same_source_generation() -> None:
    base = {
        "Reporting Area": "U.S. Residents",
        "Current MMWR Year": "2025",
        "MMWR WEEK": "1",
        "Label": "Hepatitis A, Confirmed",
        "Current week, flag": "",
    }

    with pytest.raises(ValueError, match="Conflicting US-resident NNDSS rows"):
        prepare.normalize_rows(
            [
                {**base, "Current week": "2"},
                {**base, "Current week": "3"},
            ],
            reporting_area="US RESIDENTS",
            source_name=prepare.DEFAULT_SOURCE_NAME,
            update_mode="test",
            source_file="fixture.csv",
        )


def test_api_queries_request_both_resident_label_generations() -> None:
    assert "US RESIDENTS" in prepare.DEFAULT_CSV_API_URL
    assert "U.S. RESIDENTS" in prepare.DEFAULT_CSV_API_URL
    assert "'TOTAL'" not in prepare.DEFAULT_CSV_API_URL


def test_merge_purges_only_legacy_nndss_total_rows(tmp_path) -> None:
    output = tmp_path / "history_merged.csv"

    def row(
        *, source: str, area: str, label: str, cases: str
    ) -> dict[str, str]:
        item = {column: "" for column in prepare.OUTPUT_COLUMNS}
        item.update(
            {
                "Date": "2025-01-04",
                "Diseases": label,
                "Source": source,
                "ReportingArea": area,
                "RawDiseaseLabel": label,
                "Cases": cases,
            }
        )
        return item

    prepare.write_output(
        output,
        [
            row(
                source=prepare.DEFAULT_SOURCE_NAME,
                area="Total",
                label="Hepatitis A, Confirmed",
                cases="12",
            ),
            row(
                source="US CDC NHSS",
                area="TOTAL",
                label="HIV diagnoses",
                cases="20",
            ),
        ],
    )
    resident = row(
        source=prepare.DEFAULT_SOURCE_NAME,
        area="US RESIDENTS",
        label="Hepatitis A, Confirmed",
        cases="11",
    )
    resident["PopulationScope"] = "us_residents_excluding_territories"

    merged = prepare.merge_existing_rows(output, [resident], replace=False)

    assert {(item["Source"], item["ReportingArea"], item["Cases"]) for item in merged} == {
        ("US CDC NHSS", "TOTAL", "20"),
        (prepare.DEFAULT_SOURCE_NAME, "US RESIDENTS", "11"),
    }
