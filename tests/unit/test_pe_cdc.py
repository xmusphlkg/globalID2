"""Real Peru CDC national summaries and immutable partial-history contracts."""
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from src.data.crawlers.pe import (discover_reports, parse_bulletin_pdf, report_period,
    PeruContractError, pdf_text, LISTING_URL, _integer)
from src.data.processors.pe import PeruCDCWeeklyUpdater
from src.services.ingestion.countries import pe

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/ingestion/PE/official_bulletins.zip"


def fixture(name):
    with zipfile.ZipFile(FIXTURE) as archive:
        return archive.read(name)


@pytest.fixture(scope="module")
def targets():
    return discover_reports(fixture("full_listing.html"))


@pytest.fixture(scope="module")
def parsed(targets):
    result = {}
    for year, week in [(1993, 52), (2013, 52), (2019, 51)]:
        ref = next(r for r in targets if (r["year"], r["week"]) == (year, week))
        result[year] = parse_bulletin_pdf(fixture(f"{year}w{week}.pdf"), source_url=ref["url"], reference=ref)
    return result


def test_complete_directory_includes_nonstandard_filenames_and_aliases(targets):
    assert len(targets) == 1661
    assert min(r["year"] for r in targets) == 1993
    assert next(r for r in targets if (r["year"], r["week"]) == (2026, 4))["url"].endswith(".pdf")
    aliased = next(r for r in targets if r["url"].endswith("boletin_19937.pdf"))
    assert aliased["directory_week_aliases"] == [7, 17]
    assert "directory_conflicting_week_aliases" in aliased["catalog_issues"]
    assert sum("directory_title_year_conflicts_with_url" in r["catalog_issues"] for r in targets) == 2
    assert any(r["published_date"] is None for r in targets)


def test_historical_source_calendar_and_national_scope(parsed):
    rows, audit = parsed[1993]
    assert audit["period_start"] == "1993-12-26"
    assert audit["period_end"] == "1994-01-01"
    values = {r["SourceDiseaseCode"]: r["Cases"] for r in rows}
    assert values["colera__cumulative_cases"] == "71448"
    assert values["tos_ferina__cumulative_cases"] == "940"
    assert len(rows) == 8
    assert all(r["Measure"] == "cumulative_case_notifications" and r["PeriodStart"] == "" for r in rows)
    assert all(r["SourceReportYear"] == "1993" and r["GeographyKey"] == "country:PE:national" for r in rows)


def test_unclassified_matrix_preserves_all_native_events_and_deaths(parsed):
    rows, audit = parsed[2013]
    assert len(audit["national_summary_tables"][0]["categories"]) == 27
    assert len(rows) == 76
    values = {r["SourceDiseaseCode"]: r for r in rows}
    assert values["dengue_con_senales_de_alarma__weekly_cases"]["Cases"] == "16"
    assert values["dengue_con_senales_de_alarma__cumulative_cases"]["Cases"] == "3530"
    assert values["antrax_carbunco__weekly_cases"]["Cases"] == "0"
    assert values["muerte_materna_directa__weekly_deaths"]["Measure"] == "deaths"
    assert not any(r["Measure"] == "cases" and r["RawDiseaseLabel"].startswith("Muerte") for r in rows)


def test_classified_matrix_never_sums_confirmed_probable_or_rates(parsed):
    rows, audit = parsed[2019]
    assert len(audit["national_summary_tables"][0]["categories"]) == 26
    assert len(rows) == 111
    values = {r["SourceDiseaseCode"]: r for r in rows}
    assert values["dengue_con_signos_de_alarma__weekly_cases__confirmed"]["Cases"] == "8"
    assert values["dengue_con_signos_de_alarma__weekly_cases__probable"]["Cases"] == "79"
    assert values["dengue_con_signos_de_alarma__cumulative_cases__confirmed"]["Cases"] == "1995"
    assert values["dengue_con_signos_de_alarma__cumulative_cases__probable"]["Cases"] == "775"
    assert values["malaria_p_falciparum__cumulative_cases"]["Cases"] == "4550"
    assert all(r["DiseaseCode"] == "__source_native__" for r in rows)
    assert not any(r["Cases"] in {"8.52", "13.99"} for r in rows)


@pytest.mark.parametrize("value", ["1O0", "1.23", "1 23", "-", "s/d"])
def test_ambiguous_numeric_values_are_rejected(value):
    with pytest.raises(PeruContractError):
        _integer(value)


def test_incomplete_pdf_and_unverified_period_fail_closed():
    with pytest.raises(PeruContractError, match="complete"):
        pdf_text(b"%PDF incomplete")
    with pytest.raises(PeruContractError, match="interval"):
        report_period("Semana 52 2010", reference={"year": 2010, "week": 52})
    assert report_period("Volumen 19, Número 52, 2010. Semana epidemiológica 52 al 01 de Enero de 2011", reference={"year": 2010, "week": 52},
        calendar_definition_verified=True) == (date(2010, 12, 26), date(2011, 1, 1))


def artifact(payload, tmp_path, endpoint, *, url=LISTING_URL, kind="archive_json", **metadata):
    digest = hashlib.sha256(payload).hexdigest(); path = tmp_path / digest; path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path), sha256=digest, byte_size=len(payload),
        source_url=url, kind=kind, metadata_={"endpoint": endpoint, **metadata})


def test_offline_partial_batch_retains_plan_and_detects_tampering(tmp_path, targets, monkeypatch):
    monkeypatch.setattr(pe, "read_artifact", lambda a: Path(a.relative_path).read_bytes())
    monkeypatch.setattr(pe.PeruCDCCrawler, "discover", lambda *a, **k: pytest.fail("offline parser used network"))
    ref = next(r for r in targets if (r["year"], r["week"]) == (1993, 52))
    directory = artifact(fixture("full_listing.html"), tmp_path, "directory", kind="html_report")
    pdf = artifact(fixture("1993w52.pdf"), tmp_path, "bulletin", url=ref["url"], kind="cda_pdf", reference=ref)
    audit = artifact(json.dumps({"targets": targets, "captured": [{"url": ref["url"], "sha256": pdf.sha256}],
        "errors": []}).encode(), tmp_path, "collection_audit")
    result = pe.organize([directory, pdf, audit], updater=PeruCDCWeeklyUpdater())
    assert len(result.rows) == 8
    assert len(result.source_audit["outstanding_reports"]) == 1660
    assert result.source_audit["status"] == "partial"
    with pytest.raises(PeruContractError, match="manifest"):
        pe.organize([directory, audit], updater=PeruCDCWeeklyUpdater())


def test_conflicting_official_variants_quarantine_group_and_continue(tmp_path, targets, parsed, monkeypatch):
    monkeypatch.setattr(pe, "read_artifact", lambda a: Path(a.relative_path).read_bytes())
    directory = artifact(fixture("full_listing.html"), tmp_path, "directory", kind="html_report")
    refs = targets[:2]
    reports = [artifact(f"pdf-{i}".encode(), tmp_path, "bulletin", url=r["url"], kind="cda_pdf", reference=r) for i,r in enumerate(refs)]
    base = dict(parsed[1993][0][0]); independent = dict(base, SourceDiseaseCode="independent", Cases="12")
    def parse(a, cache):
        rows = [base, independent] if a is reports[0] else [dict(base, Cases="99")]
        return rows, {"reference": a.metadata_["reference"], "status": "partial"}, None
    monkeypatch.setattr(pe, "_parse", parse)
    audit = artifact(json.dumps({"targets": targets, "captured": [{"url": r.source_url, "sha256":r.sha256} for r in reports],
        "errors": []}).encode(), tmp_path, "collection_audit")
    result = pe.organize([directory, *reports, audit], updater=PeruCDCWeeklyUpdater())
    assert [r["SourceDiseaseCode"] for r in result.rows] == ["independent"]
    assert len(result.source_audit["conflicting_observations"]) == 1


def test_same_period_variant_is_deferred_across_partial_batches(tmp_path, targets, monkeypatch):
    monkeypatch.setattr(pe, "read_artifact", lambda a: Path(a.relative_path).read_bytes())
    monkeypatch.setattr(pe, "_parse", lambda *a: pytest.fail("solitary conflicting edition parsed before sibling arrived"))
    ref = next(r for r in targets if (r["year"], r["week"]) == (2024, 52))
    directory = artifact(fixture("full_listing.html"), tmp_path, "directory", kind="html_report")
    pdf = artifact(b"source bytes preserved", tmp_path, "bulletin", url=ref["url"], kind="cda_pdf", reference=ref)
    audit = artifact(json.dumps({"targets": targets, "captured": [{"url": ref["url"], "sha256": pdf.sha256}],
        "errors": []}).encode(), tmp_path, "collection_audit")
    result = pe.organize([directory, pdf, audit], updater=PeruCDCWeeklyUpdater())
    assert result.rows == []
    assert len(result.source_audit["deferred_variant_reports"]) == 1
    assert len(result.source_audit["deferred_variant_reports"][0]["required_variants"]) == 2


def test_real_2026_multiline_cell_preserves_physical_disease_rows():
    directory = FIXTURE.parent
    provenance = json.loads((directory / "2026w13_provenance.json").read_text())
    rows, audit = parse_bulletin_pdf((directory / "2026w13_cover_summary.pdf").read_bytes(),
        source_url=provenance["source_url"], reference=provenance["reference"])
    table = audit["national_summary_tables"][0]
    assert len(table["categories"]) == 28
    assert not table["rejected_rows"]
    assert len(rows) == 113
    values = {r["SourceDiseaseCode"]: r for r in rows}
    assert values["dengue_con_signos_de_alarma__weekly_cases__confirmed"]["Cases"] == "53"
    assert values["dengue_con_signos_de_alarma__weekly_cases__probable"]["Cases"] == "157"
    assert values["dengue_con_signos_de_alarma__cumulative_cases__confirmed"]["Cases"] == "1257"
    assert values["dengue_con_signos_de_alarma__cumulative_cases__probable"]["Cases"] == "833"
    assert values["dengue_con_signos_de_alarma__cumulative_deaths"]["Cases"] == "3"
    assert values["muerte_materna_directa__weekly_deaths"]["Cases"] == "3"
    assert values["muerte_materna_directa__cumulative_deaths"]["Cases"] == "28"
    assert values["muerte_perinatal_fetal__weekly_deaths"]["Cases"] == "35"
    assert values["muerte_perinatal_fetal__cumulative_deaths"]["Cases"] == "618"
    assert all(r["Measure"] in {"deaths", "cumulative_deaths"} for r in rows if r["RawDiseaseLabel"].startswith("Muerte"))
    assert max(len(r["SourceDiseaseCode"]) for r in rows) < 180


def test_flattened_multi_disease_label_is_rejected_and_audited(monkeypatch):
    import pdfplumber
    from src.data.crawlers.pe import parse_national_summary_tables
    class Page:
        def extract_tables(self):
            return [[['Enfermedades 2025 2026 Semana 13 Acumulado Defunción I.A. (*) ' + 'Confirmados Probables ' * 4],
                ['Antrax 0 0 0 0 0 0,00 Dengue 1 2 3 4 5 0,20 6 7 8 9 10 0,30'],
                ['Tétanos 0 0 4 0 2 0,01 0 0 3 0 1 0,01']]]
    class Document:
        pages = [Page()]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: Document())
    rows, audit = parse_national_summary_tables(b"captured bytes", texts=[
        "Perú 2026 Enfermedades/eventos sujetos a vigilancia epidemiológica"],
        reference={"year": 2026, "week": 13}, source_url=LISTING_URL, digest="sha",
        start=date(2026, 3, 29), end=date(2026, 4, 4))
    assert len(rows) == 5
    assert {r["RawDiseaseLabel"] for r in rows} == {"Tétanos"}
    assert audit[0]["rejected_rows"][0]["reason"] == "category_label_contains_numeric_table_cells_or_multiple_rows"


def test_supervisor_backoff_caps_at_fifteen_minutes():
    from scripts.imports.pe_cdc_history import supervise_retry_delay
    assert [supervise_retry_delay(i) for i in range(1, 9)] == [30, 60, 120, 240, 480, 900, 900, 900]


def test_parse_cache_uses_loaded_fingerprint_after_disk_source_changes(tmp_path, monkeypatch):
    import src.data.crawlers.pe as parser
    calls = []
    monkeypatch.setattr(pe, "read_artifact", lambda a: b"immutable original bytes")
    monkeypatch.setattr(pe, "parse_bulletin_pdf", lambda *a, **k: (calls.append(True) or [], {"validated": True}))
    item = SimpleNamespace(sha256="source-sha", source_url=LISTING_URL, metadata_={"reference": {"year": 2026, "week": 13}})
    first = pe._parse(item, tmp_path)
    original_read = Path.read_bytes
    def edited_source(path):
        return b"new source on disk, old functions still loaded" if path == Path(parser.__file__) else original_read(path)
    monkeypatch.setattr(Path, "read_bytes", edited_source)
    assert pe._parse(item, tmp_path) == first
    assert calls == [True]
    document = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert document["schema"] == "pe-cdc-physical-disease-lines-v2"
    assert document["loaded_parser_sha256"] == parser.PARSER_SHA256


def test_real_2026_death_event_with_classification_columns_is_quarantined():
    from src.data.crawlers.pe import parse_national_summary_tables
    provenance = json.loads((FIXTURE.parent / "2026w17_provenance.json").read_text())
    payload = (FIXTURE.parent / "2026w17_summary.pdf").read_bytes()
    rows, audit = parse_national_summary_tables(payload, texts=pdf_text(payload),
        reference=provenance["reference"], source_url=provenance["source_url"], digest=provenance["source_sha256"],
        start=date.fromisoformat(provenance["period_start"]), end=date.fromisoformat(provenance["period_end"]))
    assert len(rows) == 111
    assert not any(r["RawDiseaseLabel"] == "Muerte perinatal - neonatal" for r in rows)
    assert audit[0]["rejected_rows"] == [{"raw": "['Muerte perinatal - neonatal 45 45 804 804 0 0,00 21 21 701 701 0 0,00']",
        "reason": "death_event_has_unverified_classification_columns"}]


def test_controller_refuses_mixed_loaded_and_disk_parser_versions(monkeypatch):
    from scripts.imports.pe_cdc_history import assert_loaded_parser_is_current
    assert_loaded_parser_is_current()
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"edited parser code")
    with pytest.raises(RuntimeError, match="restart controller"):
        assert_loaded_parser_is_current()


def test_matrix_with_unverified_source_week_header_has_no_numeric_facts():
    from src.data.crawlers.pe import parse_national_summary_tables
    provenance = json.loads((FIXTURE.parent / "2026w17_provenance.json").read_text())
    payload = (FIXTURE.parent / "2026w17_summary.pdf").read_bytes()
    rows, audit = parse_national_summary_tables(payload, texts=pdf_text(payload),
        reference={**provenance["reference"], "week": 18}, source_url=provenance["source_url"], digest=provenance["source_sha256"],
        start=date.fromisoformat(provenance["period_start"]), end=date.fromisoformat(provenance["period_end"]))
    assert rows == []
    assert audit[0]["rejected_rows"][0]["reason"] == "unverified_summary_column_header"


@pytest.mark.asyncio
async def test_routine_capture_keeps_backfill_partial_transfer_untouched(tmp_path):
    historical = tmp_path / 'historical'
    historical.mkdir()
    partial = historical / 'inflight.pdf.part'
    partial.write_bytes(b'historical incomplete transfer')
    routine = tmp_path / 'routine'
    used = []
    target = {'url': 'https://www.dge.gob.pe/report.pdf', 'year': 2026, 'week': 13}

    class Crawler:
        session = SimpleNamespace(close=lambda: None)
        def __init__(self, *, cache_directory):
            used.append(cache_directory)
        def discover(self, **options):
            return [target], b'official index'
        def download(self, reference, *, force):
            assert reference == target and force is True
            return b'%PDF complete routine snapshot', {'retrieval_mode': 'live'}

    class Writer:
        artifacts = []
        async def write(self, payload, **metadata):
            self.artifacts.append(SimpleNamespace(sha256='captured-sha', **metadata))

    result = await pe.collect(Writer(), crawler_type=Crawler,
                             download_cache_directory=routine, force=True)
    assert used == [routine]
    assert partial.read_bytes() == b'historical incomplete transfer'
    assert {a.kind for a in result} == {'html_report', 'cda_pdf', 'archive_json'}
