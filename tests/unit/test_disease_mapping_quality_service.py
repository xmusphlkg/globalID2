from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from scripts.audit_disease_mappings import (
    SEMANTIC_GOLDEN_CASE_MISMATCH,
    audit_semantic_golden_cases,
)
from src.ontology import load_disease_ontology
from src.services.disease_mapping_quality_service import (
    AGGREGATE_CHILD_DOUBLE_COUNT_RISK,
    LEGACY_FLAT_PROJECTION_LOSSY,
    MALFORMED_MAPPING_ROW,
    MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID,
    NORMALIZED_NAME_MULTIPLE_IDS,
    RESOLVED_BY_SERIES_REGISTRY,
    SERIES_BACKFILL_PENDING,
    DiseaseMappingQualityService,
    normalize_mapping_name,
)


ROOT = Path(__file__).resolve().parents[2]


def _findings(report, code):
    return [finding for finding in report["findings"] if finding["code"] == code]


def test_reviewed_semantic_golden_cases_match_production_inventory():
    assert audit_semantic_golden_cases(ROOT / "configs" / "mapping") == []


def test_semantic_golden_case_detects_source_scoped_concept_regression(tmp_path):
    path = tmp_path / "tw.csv"
    path.write_text(
        "disease_id,local_name,local_code,category,aliases,notes,data_source\n"
        "D024,桿菌性痢疾,004,Second category,Bacillary dysentery,,Taiwan\n",
        encoding="utf-8",
    )
    expectation = (
        {
            "country_code": "TW",
            "data_source": "Taiwan",
            "local_code": "004",
            "local_name": "桿菌性痢疾",
            "expected_disease_id": "D105",
        },
    )

    findings = audit_semantic_golden_cases(path.parent, expectation)

    assert len(findings) == 1
    assert findings[0]["code"] == SEMANTIC_GOLDEN_CASE_MISMATCH
    assert findings[0]["severity"] == "error"
    assert findings[0]["evidence"]["actual_disease_ids"] == ["D024"]
    assert findings[0]["evidence"]["expected_disease_id"] == "D105"
    assert findings[0]["evidence"]["matches"][0]["location"] == {
        "path": str(path),
        "row": 2,
    }


def test_enteric_fever_and_meningitis_boundaries_are_non_additive():
    registry = load_disease_ontology()
    enteric = registry.group_detail("G_ENTERIC_FEVER_SPECTRUM")
    meningitis = registry.group_detail("G_MENINGITIS_SURVEILLANCE_SPECTRUM")

    assert {item["id"] for item in enteric["concepts"]} == {
        "D026",
        "D124",
        "D234",
    }
    assert {item["id"] for item in meningitis["concepts"]} == {
        "D110",
        "D134",
        "D135",
        "D235",
    }
    for concept_id in ("D124", "D234"):
        relation = next(
            item
            for item in registry.concept_detail(concept_id)["relations"]["outgoing"]
            if item["to_ref"]["id"] == "D026"
        )
        assert relation["rollup"] is False
        assert relation["rollup_policy"] == "no_auto_rollup"
    assert registry.concept_detail("D235")["labels"]["en"] == (
        "Meningitis (all reported etiologies)"
    )
    assert registry.concept_detail("D135")["labels"]["en"] == "Bacterial meningitis"


def test_cpo_label_and_historical_series_lifecycle_are_explicit():
    registry = load_disease_ontology()
    cpo = registry.concept_detail("D133")
    assert cpo["labels"]["en"] == "Carbapenemase-producing organisms surveillance"
    assert "infection" not in cpo["labels"]["en"].casefold()

    expected_ranges = {
        "SER_HK_NOVEL_INFLUENZA_COMBINED_H5_H7_H9": (
            "2004-01-01",
            "2007-12-01",
        ),
        "SER_HK_NOVEL_INFLUENZA_H2": ("2009-01-01", "2013-12-01"),
        "SER_TW_COVID19_OLD_DEFINITION_19COV": (
            "2020-01-01",
            "2023-12-01",
        ),
        "SER_TW_COVID19_REVISED_DEFINITION_19CVS": (
            "2023-01-01",
            "2024-08-01",
        ),
    }
    for series_id, (valid_from, valid_to) in expected_ranges.items():
        series = registry.series_lookup(series_id)
        assert series["status"] == "historical"
        assert series["valid_from"] == valid_from
        assert series["valid_to"] == valid_to
        assert len(series["availability"]) == 1
        assert series["availability"][0]["valid_from"] == valid_from
        assert series["availability"][0]["valid_to"] == valid_to


def test_duplicate_relapsing_fever_and_cn_total_are_not_active_disease_concepts():
    with (ROOT / "configs" / "standard_diseases.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        catalogue = {row["disease_id"]: row for row in csv.DictReader(handle)}
    assert "deprecated duplicate of d145" in catalogue["D176"][
        "description"
    ].casefold()

    mapping_ids: set[str] = set()
    for path in (ROOT / "configs" / "mapping").glob("*.csv"):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            mapping_ids.update(row["disease_id"] for row in csv.DictReader(handle))
    assert "D176" not in mapping_ids

    registry = load_disease_ontology()
    assert "D176" not in registry.concept_ids
    assert "D999" not in registry.concept_ids
    total_series = registry.series_lookup("SER_CN_REPORTED_NOTIFIABLE_TOTAL_MONTHLY")
    assert total_series["group_id"] == "G_CN_NOTIFIABLE_DISEASE_REPORTED_TOTAL"
    assert total_series["mapping_relation"] == "aggregate"
    assert total_series["aggregation_policy"] == "reported_aggregate"


def test_hk_mapping_has_strict_source_series_csv_shape():
    path = Path(__file__).resolve().parents[2] / "configs" / "mapping" / "hk.csv"
    expected_fields = [
        "disease_id",
        "local_name",
        "local_code",
        "category",
        "aliases",
        "notes",
        "data_source",
        "source_id",
        "series_id",
    ]
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == expected_fields
        rows = list(reader)

    assert rows
    assert all(None not in row for row in rows)
    assert all(len(row) == len(expected_fields) for row in rows)
    assert all(row["notes"].startswith("Hong Kong, China CHP mapping") for row in rows)
    assert all(
        row["data_source"] == "Hong Kong, China CHP Notifiable Infectious Diseases"
        for row in rows
    )
    assert all(row["source_id"] == "SRC_HK_CHP" for row in rows)

    d016_rows = [row for row in rows if row["disease_id"] == "D016"]
    expected_series_ids = {
        "SER_HK_NOVEL_INFLUENZA_COMBINED_H2_H5_H7_H9",
        "SER_HK_NOVEL_INFLUENZA_COMBINED_H5_H7_H9",
        "SER_HK_NOVEL_INFLUENZA_H2",
        "SER_HK_NOVEL_INFLUENZA_H5",
        "SER_HK_NOVEL_INFLUENZA_H7",
        "SER_HK_NOVEL_INFLUENZA_H9",
        "SER_HK_NOVEL_INFLUENZA_SWINE",
        "SER_HK_NOVEL_INFLUENZA_VARIANT_H3N2",
        "SER_HK_NOVEL_INFLUENZA_OTHERS",
    }
    assert len(d016_rows) == 9
    assert {row["series_id"] for row in d016_rows} == expected_series_ids
    assert len({row["local_name"] for row in d016_rows}) == 9


def test_multiple_country_source_series_mapped_to_one_id_is_an_error():
    rows = [
        {
            "country_code": "AU",
            "disease_id": "D009",
            "local_name": "Hepatitis C (newly acquired)",
            "aliases": "HCV newly acquired|Acute HCV",
            "data_source": "AU NINDSS Monthly",
        },
        {
            "country_code": "AU",
            "disease_id": "D009",
            "local_name": "Hepatitis C (unspecified)",
            "aliases": "HCV unspecified",
            "data_source": "AU NINDSS Monthly",
        },
    ]

    report = DiseaseMappingQualityService().run_audit(rows)

    findings = _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["evidence"]["country_code"] == "AU"
    assert findings[0]["evidence"]["disease_id"] == "D009"
    assert findings[0]["evidence"]["source_series_count"] == 2
    assert {
        item["local_name"] for item in findings[0]["evidence"]["source_series"]
    } == {
        "Hepatitis C (newly acquired)",
        "Hepatitis C (unspecified)",
    }


def test_aliases_on_one_mapping_row_are_not_counted_as_independent_series():
    rows = [
        {
            "country_code": "US",
            "disease_id": "D007",
            "local_name": "Hepatitis A",
            "aliases": "Hep A|Hepatitis A, acute|Hepatitis, A, acute",
            "data_source": "US CDC",
        }
    ]

    report = DiseaseMappingQualityService().run_audit(rows)

    assert _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID) == []
    assert report["summary"]["source_series_row_count"] == 1


def test_shared_condition_code_keeps_case_status_components_separate():
    rows = [
        {
            "country_code": "US",
            "disease_id": "D208",
            "local_name": "Hepatitis B, chronic, Confirmed",
            "local_code": "10105",
            "data_source": "US CDC NNDSS",
        },
        {
            "country_code": "US",
            "disease_id": "D208",
            "local_name": "Hepatitis B, chronic, Probable",
            "local_code": "10105",
            "data_source": "US CDC NNDSS",
        },
    ]

    report = DiseaseMappingQualityService().run_audit(rows)

    finding = _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID)[0]
    assert finding["evidence"]["source_series_count"] == 2
    assert {
        item["local_name"] for item in finding["evidence"]["source_series"]
    } == {
        "Hepatitis B, chronic, Confirmed",
        "Hepatitis B, chronic, Probable",
    }


def test_shared_code_collision_is_resolved_by_distinct_exact_label_registry_series():
    rows = [
        {
            "country_code": "US",
            "disease_id": "D208",
            "local_name": "Hepatitis B, chronic, Confirmed",
            "local_code": "10105",
        },
        {
            "country_code": "US",
            "disease_id": "D208",
            "local_name": "Hepatitis B, chronic, Probable",
            "local_code": "10105",
        },
    ]
    registry = [
        {
            "country_code": "US",
            "source_id": "SRC_US",
            "series_id": "SER_CONFIRMED",
            "disease_id": "D208",
            "local_codes": ["10105"],
            "local_labels": ["Hepatitis B, chronic, Confirmed"],
            "status": "active",
            "availability_statuses": ["available"],
        },
        {
            "country_code": "US",
            "source_id": "SRC_US",
            "series_id": "SER_PROBABLE",
            "disease_id": "D208",
            "local_codes": ["10105"],
            "local_labels": ["Hepatitis B, chronic, Probable"],
            "status": "historical",
            "availability_statuses": ["upstream_available_ingestion_pending"],
        },
    ]

    report = DiseaseMappingQualityService().run_audit(
        rows,
        series_registry_rows=registry,
    )

    assert _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID) == []
    assert len(_findings(report, RESOLVED_BY_SERIES_REGISTRY)) == 1
    assert len(_findings(report, LEGACY_FLAT_PROJECTION_LOSSY)) == 1
    pending = _findings(report, SERIES_BACKFILL_PENDING)
    assert pending[0]["evidence"]["pending_series_ids"] == ["SER_PROBABLE"]
    assert report["summary"]["registry_coverage"] == {
        "status": "completed",
        "registry_series_count": 2,
        "collision_identities_total": 2,
        "collision_identities_bound": 2,
        "mapping_rows_explicit_series_id": 0,
        "mapping_rows_explicit_bound": 0,
    }


def test_unregistered_collision_remains_an_error_with_coverage_evidence():
    rows = [
        {"country_code": "AU", "disease_id": "D009", "local_name": "HCV new"},
        {"country_code": "AU", "disease_id": "D009", "local_name": "HCV old"},
    ]

    report = DiseaseMappingQualityService().run_audit(
        rows,
        series_registry_rows=[],
    )

    finding = _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID)[0]
    coverage = finding["evidence"]["registry_coverage"]
    assert coverage["bound_identity_count"] == 0
    assert coverage["all_identities_uniquely_bound_to_distinct_series"] is False
    assert {item["match_status"] for item in coverage["identities"]} == {"unbound"}


def test_multiple_mapping_alias_rows_with_one_explicit_series_id_are_one_identity():
    rows = [
        {
            "country_code": "US",
            "disease_id": "D007",
            "series_id": "SER_US_HEPATITIS_A",
            "local_name": "Hepatitis A, Confirmed",
        },
        {
            "country_code": "US",
            "disease_id": "D007",
            "series_id": "SER_US_HEPATITIS_A",
            "local_name": "Hepatitis, A, acute",
        },
    ]
    registry = [
        {
            "country_code": "US",
            "series_id": "SER_US_HEPATITIS_A",
            "disease_id": "D007",
            "local_codes": ["10110"],
            "local_labels": ["Hepatitis A, Confirmed"],
            "status": "active",
            "availability_statuses": ["available"],
        }
    ]

    report = DiseaseMappingQualityService().run_audit(
        rows,
        series_registry_rows=registry,
    )

    assert _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID) == []
    assert _findings(report, RESOLVED_BY_SERIES_REGISTRY) == []
    assert report["summary"]["registry_coverage"][
        "mapping_rows_explicit_bound"
    ] == 2


def test_normalized_name_assigned_to_multiple_ids_in_one_country_is_an_error():
    rows = [
        {
            "country_code": "TW",
            "disease_id": "D008",
            "local_name": "HBV",
            "aliases": "Hepatitis-B¹",
            "data_source": "TW CDC",
        },
        {
            "country_code": "tw",
            "disease_id": "D068",
            "local_name": "Acute HBV",
            "aliases": "Ｈｅｐａｔｉｔｉｓ　Ｂ",
            "data_source": "TW CDC",
        },
    ]

    report = DiseaseMappingQualityService().run_audit(rows)

    findings = _findings(report, NORMALIZED_NAME_MULTIPLE_IDS)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["evidence"]["normalized_name"] == "hepatitis b"
    assert findings[0]["evidence"]["disease_ids"] == ["D008", "D068"]
    assert {item["name_field"] for item in findings[0]["evidence"]["assignments"]} == {
        "alias"
    }


def test_identical_normalized_names_in_different_countries_are_not_ambiguous():
    rows = [
        {"country_code": "US", "disease_id": "D008", "local_name": "Hepatitis B"},
        {"country_code": "AU", "disease_id": "D068", "local_name": "Hepatitis-B"},
    ]

    report = DiseaseMappingQualityService().run_audit(rows)

    assert _findings(report, NORMALIZED_NAME_MULTIPLE_IDS) == []


def test_aggregate_and_nested_descendants_report_double_count_risk():
    rows = [
        {"country_code": "CN", "disease_id": "D006", "local_name": "病毒性肝炎"},
        {"country_code": "CN", "disease_id": "D007", "local_name": "甲型肝炎"},
        {"country_code": "CN", "disease_id": "D067", "local_name": "急性甲型肝炎"},
        {"country_code": "CN", "disease_id": "D008", "local_name": "乙型肝炎"},
    ]
    hierarchy = {
        "edges": [
            {"parent_id": "D006", "child_id": "D007"},
            {"parent_id": "D006", "child_id": "D008"},
            {"parent_id": "D007", "child_id": "D067"},
        ],
        "aggregate_ids": ["D006"],
    }

    report = DiseaseMappingQualityService().run_audit(rows, hierarchy=hierarchy)

    findings = _findings(report, AGGREGATE_CHILD_DOUBLE_COUNT_RISK)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["evidence"]["parent_disease_id"] == "D006"
    assert {
        item["disease_id"]: item["relation_depth"]
        for item in findings[0]["evidence"]["descendants"]
    } == {"D007": 1, "D008": 1, "D067": 2}
    assert report["checks"][AGGREGATE_CHILD_DOUBLE_COUNT_RISK]["edge_count"] == 3


def test_hierarchy_check_is_explicitly_skipped_when_ontology_is_absent():
    report = DiseaseMappingQualityService().run_audit(
        [{"country_code": "CN", "disease_id": "D006", "local_name": "病毒性肝炎"}]
    )

    check = report["checks"][AGGREGATE_CHILD_DOUBLE_COUNT_RISK]
    assert check == {
        "status": "skipped",
        "finding_count": 0,
        "reason": "ontology hierarchy was not provided",
    }
    assert _findings(report, AGGREGATE_CHILD_DOUBLE_COUNT_RISK) == []


def test_explicit_source_series_inventory_takes_precedence_over_mapping_rows():
    mappings = [
        {"country_code": "AU", "disease_id": "D009", "local_name": "Hepatitis C"}
    ]
    source_series = [
        {
            "country_code": "AU",
            "disease_id": "D009",
            "series_id": "hcv-new",
            "local_name": "Hepatitis C (newly acquired)",
        },
        {
            "country_code": "AU",
            "disease_id": "D009",
            "series_id": "hcv-unspecified",
            "local_name": "Hepatitis C (unspecified)",
        },
    ]

    report = DiseaseMappingQualityService().run_audit(
        mappings,
        source_series_rows=source_series,
    )

    assert len(_findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID)) == 1
    assert report["summary"]["mapping_row_count"] == 1
    assert report["summary"]["source_series_row_count"] == 2
    assert (
        report["checks"][MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID]["series_input"]
        == "source_series_rows"
    )


def test_csv_loader_adds_country_and_line_evidence(tmp_path):
    path = tmp_path / "au.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["disease_id", "local_name", "data_source"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "disease_id": "D009",
                "local_name": "Hepatitis C (newly acquired)",
                "data_source": "NINDSS",
            }
        )
        writer.writerow(
            {
                "disease_id": "D009",
                "local_name": "Hepatitis C (unspecified)",
                "data_source": "NINDSS",
            }
        )
    # This repository keeps a language-only English catalogue beside country
    # mappings; it must not appear in evidence as a fictional country ``EN``.
    (tmp_path / "en.csv").write_text(
        "disease_id,local_name,data_source\nD006,Viral Hepatitis,Catalogue\n",
        encoding="utf-8",
    )

    report = DiseaseMappingQualityService(mapping_dir=tmp_path).run_audit()

    finding = _findings(report, MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID)[0]
    assert report["summary"]["mapping_row_count"] == 2
    assert finding["evidence"]["country_code"] == "AU"
    assert [
        item["location"]["row"] for item in finding["evidence"]["source_series"]
    ] == [2, 3]
    assert all(
        item["location"]["path"] == str(path)
        for item in finding["evidence"]["source_series"]
    )


def test_csv_loader_reports_extra_dictreader_columns_as_malformed(tmp_path):
    path = tmp_path / "au.csv"
    path.write_text(
        "disease_id,local_name\nD009,Hepatitis C,unexpected,overflow\n",
        encoding="utf-8",
    )

    report = DiseaseMappingQualityService(mapping_dir=tmp_path).run_audit()

    finding = _findings(report, MALFORMED_MAPPING_ROW)[0]
    assert finding["severity"] == "error"
    assert finding["evidence"] == {
        "country_code": "AU",
        "extra_values": ["unexpected", "overflow"],
        "location": {"path": str(path), "row": 2},
    }


def test_cli_uses_registry_by_default_and_no_ontology_disables_it(tmp_path):
    path = tmp_path / "us.csv"
    path.write_text(
        "disease_id,local_name,local_code\n"
        'D208,"Hepatitis B, chronic, Confirmed",10105\n'
        'D208,"Hepatitis B, chronic, Probable",10105\n',
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(ROOT / "scripts" / "audit_disease_mappings.py"),
        "--mapping-dir",
        str(tmp_path),
        "--fail-on-error",
    ]

    protected = subprocess.run(command, check=False, capture_output=True, text=True)
    assert protected.returncode == 0, protected.stderr
    protected_report = json.loads(protected.stdout)
    assert protected_report["summary"]["by_code"][RESOLVED_BY_SERIES_REGISTRY] == 1
    assert protected_report["summary"]["by_severity"]["error"] == 0

    unprotected = subprocess.run(
        [*command, "--no-ontology"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert unprotected.returncode == 1
    unprotected_report = json.loads(unprotected.stdout)
    assert unprotected_report["summary"]["by_code"][
        MULTIPLE_SOURCE_SERIES_ONE_DISEASE_ID
    ] == 1
    assert unprotected_report["summary"]["registry_coverage"]["status"] == "skipped"


def test_report_is_deterministic_for_reordered_input():
    rows = [
        {"country_code": "AU", "disease_id": "D009", "local_name": "HCV new"},
        {"country_code": "AU", "disease_id": "D009", "local_name": "HCV old"},
        {
            "country_code": "AU",
            "disease_id": "D068",
            "local_name": "Acute HBV",
            "aliases": "Hepatitis B",
        },
        {
            "country_code": "AU",
            "disease_id": "D008",
            "local_name": "HBV",
            "aliases": "Hepatitis-B",
        },
    ]
    service = DiseaseMappingQualityService()

    first = service.run_audit(rows, hierarchy={"D009": ["D068"]})
    second = service.run_audit(reversed(rows), hierarchy={"D009": ["D068"]})

    assert first == second
    assert normalize_mapping_name("急性 C 型肝炎") == "急性 c 型肝炎"
