"""Actual national monthly PDFs; never confuse monthly and cumulative columns."""
from datetime import date
from pathlib import Path
import hashlib
import json
from types import SimpleNamespace
import pytest
import requests
from src.data.crawlers.eu_monthly import CONTRACTS, count_cell, discover_indexes, discover_reports, parse_pdf
from src.data.processors.eu_monthly import EUMonthlyUpdater

FIXTURES = Path(__file__).parents[1] / "fixtures/ingestion/EU_HIGH_FREQUENCY"


def parsed(code, **options):
    return parse_pdf((FIXTURES / (code.lower() + ".pdf")).read_bytes(), country_code=code,
        source_url=CONTRACTS[code]["url"], edition_year=options.pop("edition_year", 2026), as_of=options.pop("as_of", date(2026, 11, 17)), **options)


def test_real_czech_pdf_only_extracts_edition_year_and_single_month():
    rows, audit = parsed("CZ")
    assert len(rows) == 97 and audit["failed_count"] == 0
    salmonella = next(r for r in rows if r["SourceICDCode"] == "A02")
    assert salmonella["Cases"] == "1198"
    assert {r["Date"] for r in rows} == {"2026-10-01"}
    assert all(r["PeriodEnd"] == "2026-10-31" and r["Frequency"] == "monthly" for r in rows)
    assert next(r for r in rows if r["SourceICDCode"] == "A00")["Cases"] == "0"


def test_real_estonian_pdf_reads_month_total_instead_of_year_to_date_or_counties():
    rows, audit = parsed("EE")
    assert len(rows) == 74 and audit["failed_count"] == 0
    salmonella = next(r for r in rows if r["SourceICDCode"] == "A02")
    assert salmonella["Cases"] == "9"  # YTD=115, Tallinn=5; neither is the national month.
    assert all(r["GeographyKey"] == "country:EE:national" for r in rows)
    assert all(r["Measure"] == "case_notifications" and r["Unit"] == "count" for r in rows)
    assert all(r["RawSHA256"] and r["SourcePage"] for r in rows)


@pytest.mark.parametrize("code", ["CZ", "EE"])
def test_pdf_year_and_open_month_fail_closed(code):
    with pytest.raises(ValueError, match="edition year"):
        parsed(code, edition_year=2025)
    with pytest.raises(ValueError, match="closed month"):
        parsed(code, as_of=date(2026, 10, 31))


def test_czech_discovery_uses_actual_year_links_and_excludes_regions_and_cumulative():
    payload = b'<a href="/publikace-szu/data/infekce-v-cr/rok-2026/">2026</a>'
    indexes = discover_indexes(payload, country_code="CZ", url=CONTRACTS["CZ"]["url"], start_year=2026)
    assert indexes == [{"url": "https://szu.gov.cz/publikace-szu/data/infekce-v-cr/rok-2026/", "edition_year": 2026}]
    pdfs = b'<a href="/Tabulka_rijen_2026.pdf">Cases</a><a href="/Tabulka_leden-rijen_2026.pdf">Cumulative</a><a href="/Tabulka_rijen_2026_kraje.pdf">Regions</a>'
    refs, excluded = discover_reports(pdfs, country_code="CZ", url=indexes[0]["url"], start_year=2026, edition_year=2026)
    assert len(refs) == 1 and "Tabulka_rijen_2026.pdf" in refs[0]["url"]
    assert len(excluded) == 2
    january, _ = discover_reports(b'<a href="/Tabulka_leden_2026.pdf">January</a>', country_code="CZ", url=indexes[0]["url"], start_year=2026, edition_year=2026)
    assert len(january) == 1  # Single January is not a January-to-date range.


def test_source_links_cannot_leave_official_host():
    with pytest.raises(ValueError, match="official host"):
        discover_indexes(b'<a href="https://evil.example/infekce-v-cr/">2026</a>', country_code="CZ", url=CONTRACTS["CZ"]["url"], start_year=2026)


def test_missing_zero_and_invalid_counts_have_distinct_semantics():
    assert count_cell("") == ("", "source_missing")
    assert count_cell("nd1") == ("", "source_missing")
    assert count_cell("0") == ("0", "reported")
    assert count_cell("1 198") == ("1198", "reported")
    for token in ["-1", "1.5", "1 19", "1O", "0*tail"]:
        with pytest.raises(ValueError):
            count_cell(token)


def test_monthly_updaters_refresh_published_history_and_do_not_apply_planner_month_filter():
    for code in CONTRACTS:
        rows, _ = parsed(code); updater = EUMonthlyUpdater(country_code=code)
        result = updater.prepare_rows(rows, months=[(2026, 11)])
        assert len(result.rows) == len(rows)
        assert updater.public_release_enabled is True
        assert updater.ontology_source_id == CONTRACTS[code]["source_id"]


def test_monthly_frozen_discovery_replay_checks_manifest_and_never_uses_network(tmp_path, monkeypatch):
    from src.services.ingestion.countries.eu_monthly import organize
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("offline monthly replay accessed network"))
    root = CONTRACTS["EE"]["url"]
    report_url = "https://www.terviseamet.ee/NH_haigestumine_10_2026.pdf"
    index = f'<a href="{report_url}">Haigestumine 2026</a>'.encode()
    references, excluded = discover_reports(index, country_code="EE", url=root, start_year=2026)
    artifacts = []

    def archive(payload, kind, url, metadata=None):
        sha = hashlib.sha256(payload).hexdigest()
        path = tmp_path / "data/raw/test" / (sha + ".bin")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
        a = SimpleNamespace(relative_path=str(path), source_url=url, kind=kind, sha256=sha, byte_size=len(payload), metadata_=metadata or {})
        artifacts.append(a)
        return a

    archive(index, "eu_monthly_index", root)
    pdf = archive((FIXTURES / "ee.pdf").read_bytes(), "eu_monthly_pdf", report_url)
    plan = {"country_code": "EE", "start_year": 2026, "end_year": None, "references": references,
        "captured": [{"url": report_url, "sha256": pdf.sha256}], "excluded": excluded, "errors": [], "retrieved_at": "2026-11-17T00:00:00Z"}
    archive(json.dumps(plan).encode(), "eu_monthly_plan", root)
    result = organize(artifacts, updater=EUMonthlyUpdater(country_code="EE"))
    assert len(result.rows) == 74 and result.source_audit["coverage_complete"] is True
    with pytest.raises(ValueError, match="artifact manifest"):
        organize([a for a in artifacts if a is not pdf], updater=EUMonthlyUpdater(country_code="EE"))
    Path(pdf.relative_path).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        organize(artifacts, updater=EUMonthlyUpdater(country_code="EE"))
