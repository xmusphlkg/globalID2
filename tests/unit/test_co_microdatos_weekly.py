"""Microdata counts keep source classifications and reject unsafe row units."""
import hashlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

from src.data.crawlers.co_bes import BESCover, BESReport, normalize_annex_cells
from src.data.crawlers.co_microdatos import (
    MicrodataContractError,
    aggregate_workbook,
    archive_records,
)

HEADERS = ["CONSECUTIVE", "COD_EVE", "SEMANA", "ANO", "TIP_CAS",
           "Estado_final_de_caso", "AJUSTE", "COD_PAIS_O"]


def workbook(tmp_path, records=None, event="220", headers=HEADERS):
    path = tmp_path / "original.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in records or [[1, '="220"', '="53"', '="2025"', 2, "3    ", 7, '="170"']]:
        sheet.append(row)
    book.save(path)
    book.close()
    return path, {"year": 2025, "event_code": event, "event_name": "Dengue grave",
                  "byte_size": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "source_url": f"https://portalsivigila.ins.gov.co/Microdatos/Datos_2025_{event}.xlsx",
                  "source_modified": "2026-08-07T16:02:28Z", "path": path.name}


def test_excel_literal_codes_week53_and_reconciliation(tmp_path):
    path, record = workbook(tmp_path, [
        [1, '="220"', '="53"', '="2025"', 2, "3    ", 7, '="170"'],
        [2, 220, 53, 2025, 2, 3, 7, 170],
    ])
    result = aggregate_workbook(path, record)
    assert result["audit"]["aggregated_records"] == 2
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["Cases"] == "2" and row["Measure"] == "derived_notification_record_count"
    assert row["PublicReleaseEnabled"] == "false"
    dims = json.loads(row["Dimensions"])
    assert dims["source_epi_week"] == 53
    assert dims["date_role"] == "synthetic_week_ordinal_anchor_not_event_date"
    assert dims["source_final_case_status"] == "3"
    assert row["RawSHA256"] == record["sha256"]


def test_discarded_foreign_and_missing_classification_stay_separate(tmp_path):
    path, record = workbook(tmp_path, [
        [1, 220, 20, 2025, 2, 3, 7, 170],
        [2, 220, 20, 2025, 2, 6, 6, 170],
        [3, 220, 20, 2025, 2, 3, 7, 862],
        [4, 220, 20, 2025, None, None, 0, None],
    ])
    result = aggregate_workbook(path, record)
    assert len(result["rows"]) == 4
    assert result["audit"]["observed_weeks"] == [20]
    assert result["audit"]["zero_filled"] is False
    assert result["audit"]["case_status_filtered"] is False
    assert {json.loads(r["Dimensions"])["source_occurrence_country"] for r in result["rows"]} == {
        "170", "862", "__missing__"}


@pytest.mark.parametrize("index,value", [(1, 210), (2, 0), (2, 54), (2, "=1+2"),
                                         (3, 2024), (5, "=SUM(1,2)"), (0, None)])
def test_any_invalid_record_withholds_whole_workbook(tmp_path, index, value):
    bad = [2, 220, 20, 2025, 2, 3, 7, 170]
    bad[index] = value
    path, record = workbook(tmp_path, [[1, 220, 20, 2025, 2, 3, 7, 170], bad])
    result = aggregate_workbook(path, record)
    assert result["rows"] == []
    assert result["audit"]["status"] == "quarantined_rows"
    assert result["audit"]["invalid_records"] == 1


def test_duplicate_notification_identity_is_not_silently_counted_twice(tmp_path):
    row = [1, 220, 20, 2025, 2, 3, 7, 170]
    path, record = workbook(tmp_path, [row, row])
    result = aggregate_workbook(path, record)
    assert not result["rows"]
    assert result["audit"]["row_errors"] == {"duplicate_record_identity": 1}


@pytest.mark.parametrize("event", ["349", "995", "998"])
def test_collective_forms_never_count_rows_as_cases(tmp_path, event):
    path, record = workbook(tmp_path, [[event, 20, 2025, 400]], event=event,
                            headers=["cod_eve", "semana", "año", "tot_irag"])
    result = aggregate_workbook(path, record)
    assert not result["rows"]
    assert result["audit"]["status"] == "quarantined_collective_form"


def test_modified_original_is_rejected_even_if_filename_does_not_change(tmp_path):
    path, record = workbook(tmp_path)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(MicrodataContractError, match="SHA-256"):
        aggregate_workbook(path, record)


def test_archive_missing_year_and_duplicate_event_fail_closed(tmp_path):
    _, record = workbook(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    catalogue = snapshot / "catalogue.json"
    catalogue.write_text(json.dumps({"d": {"results": [{"A_x00f1_o": "2025", "CodigoEvento": "220",
        "FileRef": "/Microdatos/Datos_2025_220.xlsx", "Modified": record["source_modified"],
        "File": {"Length": record["byte_size"]}}]}}))
    (snapshot / "manifest.json").write_text(json.dumps({"file_count": 1, "artifacts": [{
        "path": "snapshot/catalogue.json", "byte_size": catalogue.stat().st_size,
        "sha256": hashlib.sha256(catalogue.read_bytes()).hexdigest()}]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "complete", "catalogue_snapshot": "snapshot", "files": [record]}))
    assert len(archive_records(tmp_path, start_year=2025, end_year=2025)) == 1
    with pytest.raises(MicrodataContractError, match="No published"):
        archive_records(tmp_path, start_year=2026, end_year=2026)
    manifest.write_text(json.dumps({"status": "complete", "catalogue_snapshot": "snapshot", "files": [record, record]}))
    with pytest.raises(MicrodataContractError, match="Duplicate"):
        archive_records(tmp_path, start_year=2025, end_year=2025)


def test_bes_dengue_observed124_is_never_weekly682_or_difference25():
    report = BESReport("https://www.ins.gov.co/buscador-eventos/BES-2025-20.pdf",
                       "https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx",
                       "BES", 2025)
    cell = {"pdf_sha256": "a" * 64, "event_label_raw": "Dengue grave",
            "geography_label_raw": "Total nacional", "accumulated_header_raw": "Acumulado 2025",
            "accumulated_value_raw": "682", "page_1_based": 30,
            "event_group_position_1_based": 1,
            "comparison_cells": [{"header_raw": "Observado", "value_raw": "124",
                                  "period_type": "event_specific_comparison_window_unverified"}]}
    rows, audit = normalize_annex_cells([cell], report, BESCover(2025, 20, date(2025, 5, 17)))
    assert rows[0]["Cases"] == "682"
    assert rows[0]["Measure"] == "source_reported_ytd_event_count"
    assert rows[0]["WeeklyIncidenceEligible"] == "false"
    assert json.loads(rows[0]["SourceComparisonCells"])[0]["value_raw"] == "124"
    assert audit["weekly_incidence_coverage_complete"] is False
    assert audit["cumulative_differencing_enabled"] is False


async def test_microdata_task_uses_separate_private_source_pipeline(monkeypatch):
    from src.core.source_scopes import (
        scope_from_data_source,
        source_options_for_country,
    )
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import runner

    run = AsyncMock(return_value=object())
    monkeypatch.setattr(runner, "run_surveillance", run)
    source = "co_ins_sivigila_microdatos"
    assert scope_from_data_source("SRC_CO_INS_SIVIGILA_MICRODATOS") == source
    assert source in {o["value"] for o in source_options_for_country("CO")}
    await CrawlService().execute(task=SimpleNamespace(input_data={"start_year": 2025, "end_year": 2025}),
                                 country_code="CO", source=source, force=False, process=True,
                                 save_raw=True, fill_missing=False)
    args = run.call_args.kwargs
    assert args["updater"].ontology_source_id == "SRC_CO_INS_SIVIGILA_MICRODATOS"
    assert args["connector"].__name__.endswith("co_microdatos_refresh")
    assert args["process"] is True
    assert args["updater"].public_release_enabled is False
    assert args["connector_options"] == {"start_year": 2025, "end_year": 2025, "force": False}


def test_public_sources_explain_breaks_even_with_stale_database_config():
    from src.generation.site_data_about import build_country_source_info

    info = build_country_source_info("CO", database_config={
        "crawler_config": {"sources": ["co_ins_sivigila_historical"]}, "notes": "old notes"})
    sources = {s["scope"]: s for s in info["sources"]}
    assert len(sources) == 6
    assert "who_flunet_weekly" in sources
    assert sources["co_ins_sivigila_current"]["url"].endswith("wns8-p38i")
    assert sources["co_ins_sivigila_current"]["coverage_years"] == [2022, 2024]
    assert "没有 2025 或 2026" in sources["co_ins_sivigila_current"]["description_zh"]
    assert "Acumulado" in sources["co_ins_bes_weekly"]["description_zh"]
    assert "No same-structure" in info["notes"]
