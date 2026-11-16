"""INS BES discovery and source-native cumulative table contracts."""

import json
from datetime import date
from pathlib import Path

import pytest

from src.data.crawlers.co_bes import (
    BESContractError,
    BESCover,
    BESReport,
    discover_archive_links,
    discover_sharepoint_files,
    discover_sharepoint_list_items,
    inspect_pdf,
    normalize_annex_cells,
    parse_cover_identity,
    parse_observations,
    sharepoint_library_context,
    sharepoint_list_url,
)


def test_archive_discovers_only_official_library_pdfs():
    payload = b"""<html><body>
      <a href="/buscador-eventos/BoletinEpidemiologico/BES-2025-01.pdf">Week 1</a>
      <a href="https://other.example/BoletinEpidemiologico/BES-2025-02.pdf">other</a>
      <a href="/other/BES-2025-03.pdf">unrelated</a>
      <a href="/buscador-eventos/BoletinEpidemiologico/BES-2025-01.pdf">duplicate</a>
    </body></html>"""
    reports = discover_archive_links(payload)
    assert len(reports) == 1
    assert reports[0].pdf_url.endswith("/BES-2025-01.pdf")
    with pytest.raises(BESContractError, match="Complete"):
        discover_archive_links(payload[:50])


def test_sharepoint_listing_uses_actual_file_paths():
    reports = discover_sharepoint_files(
        {
            "value": [
                {
                    "Name": "BES-2026-01.pdf",
                    "ServerRelativeUrl": "/buscador-eventos/BoletinEpidemiologico/BES-2026-01.pdf",
                },
                {
                    "Name": "photo.jpg",
                    "ServerRelativeUrl": "/buscador-eventos/BoletinEpidemiologico/photo.jpg",
                },
            ]
        }
    )
    assert len(reports) == 1
    assert reports[0].title == "BES-2026-01.pdf"


@pytest.mark.parametrize("listing_url", [
    "http://www.ins.gov.co/buscador-eventos/list",
    "https://user:secret@www.ins.gov.co/buscador-eventos/list",
    "https://www.ins.gov.co:8443/buscador-eventos/list",
    "https://www.ins.gov.co/buscador-eventos/%2e%2e/private",
])
def test_discovery_rejects_untrusted_listing_provenance(listing_url):
    payload = b'<html><a href="/buscador-eventos/report.pdf">BES</a></html>'
    with pytest.raises(BESContractError, match="listing URL"):
        discover_archive_links(payload, listing_url=listing_url)
    with pytest.raises(BESContractError, match="listing URL"):
        discover_sharepoint_files({"value": []}, listing_url=listing_url)


@pytest.mark.parametrize("pdf_path", [
    "/buscador-eventos/%2e%2e/private.pdf",
    "/buscador-eventos/%252e%252e/private.pdf",
    "/buscador-eventos/%5cprivate.pdf",
])
def test_discovery_rejects_encoded_pdf_path_traversal(pdf_path):
    payload = f'<html><a href="{pdf_path}">BES</a></html>'.encode()
    assert discover_archive_links(payload) == []
    assert discover_sharepoint_files({"value": [{"ServerRelativeUrl": pdf_path}]}) == []


def test_cover_requires_unique_week_and_ins_identity():
    text = "Instituto Nacional de Salud\nBoletín Epidemiológico Semanal\nSemana epidemiológica 42 de 2025"
    cover = parse_cover_identity(text)
    assert (cover.year, cover.week) == (2025, 42)
    with pytest.raises(BESContractError, match="INS publisher"):
        parse_cover_identity(text.replace("Instituto Nacional de Salud", "Otro editor"))
    with pytest.raises(BESContractError, match="unique"):
        parse_cover_identity(text + "\nSemana epidemiológica 43 de 2025")
    year_end = ("Instituto Nacional de Salud Boletín Epidemiológico Semanal "
                "Semana 01 Epidemiológica 29 de diciembre 2024 al 04 de enero de 2025")
    assert parse_cover_identity(year_end) == BESCover(2025, 1, date(2025, 1, 4))


def test_annex_cell_normalizer_preserves_ytd_identity_and_ambiguous_groups():
    report = BESReport(
        "https://www.ins.gov.co/buscador-eventos/BoletinEpidemiologico/BES.pdf",
        "https://www.ins.gov.co/buscador-eventos/list", "BES.pdf", 2025)
    cover = BESCover(2025, 24, date(2025, 6, 14))
    base = {"pdf_sha256": "a" * 64, "page_1_based": 41,
            "event_label_raw": "IRAG inusitado", "case_definition_raw": "Casos probables",
            "geography_label_raw": "Total nacional", "accumulated_header_raw": "Acumulado 2025"}
    cells = [base | {"event_group_position_1_based": 1,
                     "accumulated_value_raw": "1,234"},
             base | {"event_group_position_1_based": 4,
                     "accumulated_value_raw": "1.235"}]
    rows, audit = normalize_annex_cells(cells, report, cover)
    assert [row["Cases"] for row in rows] == ["1234", "1235"]
    assert {row["SourceDiseaseCode"] for row in rows} == {
        "bes_irag_inusitado__annex_group_1", "bes_irag_inusitado__annex_group_4"}
    assert all(row["SourceLabelAmbiguous"] for row in rows)
    assert all(json.loads(row["Dimensions"]) == {
        "source_epi_year": 2025, "source_epi_week": 24,
        "source_period_type": "year_to_date_at_epi_week"} for row in rows)
    assert all(row["AuthoritativeRevision"] == "true" for row in rows)
    assert audit["unparsed_cells"] == 0
    with pytest.raises(BESContractError, match="not source-year cumulative"):
        normalize_annex_cells([cells[0] | {"accumulated_header_raw": "Observado"}], report, cover)
    unresolved, malformed = normalize_annex_cells([cells[0] | {
        "accumulated_value_raw": "1,234.567"}], report, cover)
    assert malformed["unparsed_cells"] == 1
    assert unresolved[0]["Cases"] == ""
    assert unresolved[0]["MissingValueStatus"] == "source_unreadable"
    assert unresolved[0]["SourceAccumulatedValueRaw"] == "1,234.567"


def test_numeric_parser_requires_complete_official_pdf():
    report = BESReport("https://www.ins.gov.co/buscador-eventos/BoletinEpidemiologico/BES.pdf",
                       "https://www.ins.gov.co/buscador-eventos/list", "BES.pdf")
    with pytest.raises(BESContractError, match="Complete"):
        parse_observations(b"%PDF synthetic", report)


@pytest.mark.parametrize("filename,year,week,expected_date,accident_count", [
    ("2025_Boletin_epidemiologico_semana_53.pdf", 2025, 53, "2026-01-03", "5397"),
    ("2026_Boletin_epidemiologico_semana_44.pdf", 2026, 44, "2026-11-07", "4276"),
])
def test_official_dashboard_samples_retain_ytd_and_source_week(
    filename, year, week, expected_date, accident_count,
):
    root = Path(__file__).resolve().parents[2]
    pdf = root / "data/raw/co/network-diagnostics/20261116-wireguard" / filename
    if not pdf.exists():
        pytest.skip("Official INS diagnostic PDF is not present")
    report = BESReport("https://www.ins.gov.co/buscador-eventos/BoletinEpidemiologico/" + filename,
                       "https://www.ins.gov.co/buscador-eventos/list", filename, year)
    rows, audit = parse_observations(pdf.read_bytes(), report)
    national = next(row for row in rows if row["GeographyKey"] == "country:CO:national"
                    and row["RawDiseaseLabel"] == "Accidente ofídico")
    assert national["Cases"] == accident_count
    assert national["Date"] == expected_date
    assert national["Year"] == str(year)
    assert national["PeriodType"] == "source_epi_year_to_date_at_week"
    assert (audit["year"], audit["week"]) == (year, week)
    assert len(audit["table_pages"]) == 8
    assert not audit["unparsed_groups"] and not audit["unparsed_cells"]
    if year == 2025:
        assert audit["explicit_missing_cells"] == 1
    else:
        ambiguous = [row for row in rows if "__annex_group_" in row["SourceDiseaseCode"]]
        assert len(ambiguous) == 78
        assert {row["RawDiseaseLabel"] for row in ambiguous} == {"IRAG inusitado"}


@pytest.mark.parametrize("title, expected", [
    ("2025_Boletin_epidemiologico_semana_1.pdf", BESCover(2025, 1, date(2025, 1, 4))),
    ("2025_Boletin_epidemiologico_semana_10.pdf", BESCover(2025, 10, date(2025, 3, 8))),
    ("2025_Boletin_epidemiologico_semana_17.pdf", BESCover(2025, 17, date(2025, 4, 26))),
    ("2025_Boletin_epidemiologico_semana_45.pdf", BESCover(2025, 45, date(2025, 11, 8))),
])
def test_official_2025_cover_ignores_topic_week(title, expected):
    root = Path(__file__).resolve().parents[2]
    manifest = root / "data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json"
    if not manifest.exists():
        pytest.skip("Official INS history manifest is not present")
    document = json.loads(manifest.read_bytes())
    entry = next((item for item in document["artifacts"]
                  if item.get("metadata_", {}).get("report", {}).get("title")
                  == title), None)
    if entry is None:
        pytest.skip("Official 2025 PDF is not present")
    cover = inspect_pdf((root / entry["relative_path"]).read_bytes())
    assert cover == expected


def test_official_malformed_print_remains_unknown_with_raw_value():
    root = Path(__file__).resolve().parents[2]
    manifest = root / "data/raw/co/bes-history/20261116-production-backfill-v4/manifest.json"
    if not manifest.exists():
        pytest.skip("Official INS history manifest is not present")
    document = json.loads(manifest.read_bytes())
    entry = next((item for item in document["artifacts"]
                  if item.get("metadata_", {}).get("report", {}).get("title")
                  == "2025_Boletin_epidemiologico_semana_34.pdf"), None)
    if entry is None:
        pytest.skip("Official 2025 week-34 PDF is not present")
    report = BESReport(**entry["metadata_"]["report"])
    rows, audit = parse_observations((root / entry["relative_path"]).read_bytes(), report)
    national = next(row for row in rows if row["GeographyKey"] == "country:CO:national"
                    and row["RawDiseaseLabel"] == "Tos ferina")
    assert national["Cases"] == ""
    assert national["MissingValueStatus"] == "source_unreadable"
    assert national["SourceAccumulatedValueRaw"] == "4,95"
    assert audit["unparsed_cells"] >= 1


def test_saved_official_sharepoint_snapshot_has_97_distinct_reports():
    """Check the captured 2025/26 library shape when local source evidence exists."""
    root = Path(__file__).resolve().parents[2]
    evidence = root / "data/raw/co/network-diagnostics/20261116-wireguard"
    if not (evidence / "list-items.json").exists():
        pytest.skip("Official INS diagnostic snapshot is not present")
    import json

    guid, folder = sharepoint_library_context((evidence / "ins-archive.html").read_bytes())
    url = sharepoint_list_url(guid, 2025, 2026)
    document = json.loads((evidence / "list-items.json").read_bytes())
    reports = discover_sharepoint_list_items(
        document, listing_url=url, folder=folder, start_year=2025, end_year=2026)
    assert len(reports) == len({report.pdf_url for report in reports}) == 97
    assert sum(report.listed_year == 2025 for report in reports) == 53
    assert sum(report.listed_year == 2026 for report in reports) == 44
