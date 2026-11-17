"""BES reconciliation retains source years and checks facts, not just totals."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import verify_co_bes_history as verifier


def source_row(record_id="a", cases="1"):
    return {
        "SourceRecordID": record_id, "Date": "2026-01-03",
        "GeographyKey": "country:CO:source-department:Amazonas",
        "SourceDiseaseCode": "bes_accidente_ofidico",
        "DefinitionVersion": "bes-cumulative-annex-v1", "Cases": cases,
        "Dimensions": json.dumps({"source_epi_year": 2025, "source_epi_week": 53}),
        "RawSHA256": "pdfhash", "SourceURL": "https://www.ins.gov.co/buscador-eventos/report.pdf",
        "PeriodType": "source_epi_year_to_date_at_week", "Measure": "source_reported_ytd_event_count",
        "ReportingBasis": "bes_department_dashboard", "Frequency": "weekly", "Unit": "count",
    }


def db_row(row, *, value=None, metric=None):
    return (row, float(row["Cases"]) if value is None else value,
            datetime.fromisoformat(row["Date"]).replace(tzinfo=timezone.utc),
            row["GeographyKey"], verifier.expected_record(row)[6], row["SourceDiseaseCode"],
            row["DefinitionVersion"], "count", "count", metric or row["Measure"],
            row["ReportingBasis"], "weekly", False)


def snapshot(rows):
    return verifier.SourceSnapshot(
        {row["SourceRecordID"]: (verifier.source_year(row), verifier.expected_record(row)) for row in rows},
        {2025: len(rows)}, {}, True, True, 1, 1)


def fake_database(monkeypatch, rows):
    class Database:
        async def execute(self, query):
            assert "READ ONLY" in str(query)

        async def stream(self, query, params):
            assert params == {"source_id": "SRC_CO_INS_BES"}
            assert "obs.time >=" not in str(query)
            async def stream():
                for row in rows:
                    yield row
            return stream()

    @asynccontextmanager
    async def database():
        yield Database()

    async def dispose():
        pass

    monkeypatch.setattr(verifier, "get_db", database)
    monkeypatch.setattr(verifier, "dispose_database", dispose)


def test_source_year_uses_epidemiological_year_across_calendar_boundary():
    row = source_row()
    assert row["Date"].startswith("2026")
    assert verifier.source_year(row) == 2025
    row["Dimensions"] = '{}'
    with pytest.raises(ValueError, match="epidemiological year"):
        verifier.source_year(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,field", [("value", "value"), ("metric", "metric_type")])
async def test_physical_value_and_cumulative_semantics_checked_when_raw_matches(monkeypatch, change, field):
    row = source_row()
    fake_database(monkeypatch, [db_row(row, **({"value": 99} if change == "value" else {"metric": "weekly_new_cases"}))])
    report = await verifier.compare_database(snapshot([row]), start_year=2025, end_year=2026)
    assert report["mismatched_rows"] == 1
    assert report["mismatched_row_examples"][0]["fields"] == [field]
    assert report["complete"] is False


@pytest.mark.asyncio
async def test_equal_totals_do_not_hide_missing_duplicate_and_extra_source_records(monkeypatch):
    a, b = source_row("a", "1"), source_row("b", "1")
    extra = source_row("extra", "0")
    fake_database(monkeypatch, [db_row(a), db_row(a), db_row(extra)])
    report = await verifier.compare_database(snapshot([a, b]), start_year=2025, end_year=2026)
    assert report["missing_database_rows"] == 1
    assert report["duplicate_database_record_ids"] == 1
    assert report["unexpected_database_rows"] == 1
    assert report["complete"] is False


@pytest.mark.asyncio
async def test_matching_facts_cannot_mark_unrepresented_originals_as_complete(monkeypatch):
    row = source_row()
    source = snapshot([row]); source.all_captured_reports_have_facts = False
    fake_database(monkeypatch, [db_row(row)])
    report = await verifier.compare_database(source, start_year=2025, end_year=2026)
    assert report["facts_match"] is True
    assert report["complete"] is False
    assert report["all_narrative_tables_imported"] is False
    assert report["public_release_enabled"] is False


def test_snapshot_rejects_duplicate_source_identity_and_unlinked_pdf(monkeypatch):
    row = source_row()
    artifact = SimpleNamespace(metadata_={"endpoint": "pdf", "report": {"pdf_url": row["SourceURL"]}},
                               source_url=row["SourceURL"], sha256="pdfhash")
    plan = {"requested_start_year": 2025, "requested_end_year": 2026,
            "reports": [{"pdf_url": row["SourceURL"]}], "failed_downloads": []}
    plan_artifact = SimpleNamespace(metadata_={"endpoint": "collection_plan"})
    monkeypatch.setattr(verifier, "read_artifact", lambda a: json.dumps(plan).encode())
    with pytest.raises(ValueError, match="Duplicate BES"):
        verifier.build_snapshot([artifact, plan_artifact], [row, row], start_year=2025, end_year=2026)
    row["RawSHA256"] = "different_original"
    with pytest.raises(ValueError, match="not linked"):
        verifier.build_snapshot([artifact, plan_artifact], [row], start_year=2025, end_year=2026)


def test_official_blank_is_explicit_null_and_never_zero():
    row = source_row(cases="")
    with pytest.raises(ValueError):
        verifier.expected_record(row)
    row["MissingValueStatus"] = "source_missing"
    assert verifier.expected_record(row)[5] is None
    row["Cases"] = "0"
    assert verifier.expected_record(row)[5] == 0


def test_independent_original_pdf_cell_check_catches_wrong_column():
    row = source_row(cases="76")
    row.update(RawSHA256="8aa142bd19e17ed167f063e363e97ddf9c5938753aa5a0c773e83d822e0c162d",
               RawDiseaseLabel="Accidente ofídico", Geography="Total nacional")
    # 76 is the printed expectation; the original annual cumulative cell is 5397.
    with pytest.raises(ValueError, match="original PDF cell check failed"):
        verifier.check_original_cells([row])


@pytest.mark.asyncio
async def test_parser_gaps_prevent_coverage_success_even_when_all_parsed_facts_match(monkeypatch):
    row = source_row()
    source = snapshot([row])
    source.source_audit = {"coverage_complete": False, "unparsed_cells": 1}
    fake_database(monkeypatch, [db_row(row)])
    report = await verifier.compare_database(source, start_year=2025, end_year=2026)
    assert report["facts_match"] is True
    assert report["parser_coverage_complete"] is False
    assert report["complete"] is False


@pytest.mark.asyncio
async def test_complete_original_cells_do_not_require_resolving_ambiguous_source_labels(monkeypatch):
    row = source_row()
    source = snapshot([row])
    source.source_audit = {"coverage_complete": False, "parsed_reports": 1,
                           "discovered_reports": 1, "failed_reports": [],
                           "parser_gaps": [], "unparsed_groups": 0, "unparsed_cells": 0}
    source.ambiguous_source_label_cells = 1
    fake_database(monkeypatch, [db_row(row)])
    report = await verifier.compare_database(source, start_year=2025, end_year=2026)
    assert report["facts_match"] is True
    assert report["fixed_annex_cells_complete"] is True
    assert report["complete"] is True
    assert report["coverage_scope"] == "source_native_cumulative_dashboard_cells"
    assert report["source_definitions_fully_resolved"] is False
    assert report["all_narrative_tables_imported"] is False


@pytest.mark.asyncio
async def test_incremental_reconciliation_filters_report_url_not_hash_or_calendar_year(monkeypatch):
    row = source_row()

    class Database:
        async def execute(self, query):
            assert "READ ONLY" in str(query)

        async def stream(self, query, params):
            assert "obs.raw_data ->> 'SourceURL' IN" in str(query)
            assert "RawSHA256" not in str(query)
            assert "obs.time >=" not in str(query)
            assert params["report_urls"] == [row["SourceURL"]]

            async def stream():
                yield db_row(row)
            return stream()

    @asynccontextmanager
    async def database():
        yield Database()

    async def dispose():
        pass

    monkeypatch.setattr(verifier, "get_db", database)
    monkeypatch.setattr(verifier, "dispose_database", dispose)
    result = await verifier.compare_database(snapshot([row]), start_year=2025, end_year=2026,
                                              report_urls=[row["SourceURL"]])
    assert result["facts_match"] is True
    assert result["comparison_scope"] == "snapshot_reports"
