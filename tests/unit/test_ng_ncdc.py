"""Real NCDC reports preserve classification, period and replay semantics."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.data.crawlers.ng import DEFAULT_SCOPE, NigeriaNCDCContractError, discover_categories, discover_reports, discover_weekly_pages, extract_pdf_pages, parse_report, parse_sitrep_pages, parse_weekly_pages
from src.data.processors.ng import NigeriaNCDCUpdater
from src.services.ingestion.countries import ng

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/NG"
REPORT = {"url": "https://ncdc.gov.ng/themes/common/docs/wers/700_1791361960.pdf", "listing_url": "https://ncdc.gov.ng/reports/weekly/2026/8/34", "kind": "weekly", "year": 2026, "week": 34}


def real_rows():
    return parse_report((FIXTURES / "wer_2026_w34.pdf").read_bytes(), REPORT)[0]


def test_complete_real_modern_report_keeps_ten_categories_and_separate_measures():
    rows = real_rows()
    assert len(rows) == 85
    assert len({json.loads(r["Dimensions"])["source_disease_code"] for r in rows}) == 10
    by_code = {(r["SourceDiseaseCode"], r["SourcePeriodYear"]): r for r in rows}
    assert by_code[("wer:lassa_fever:suspected:weekly:cases", "2026")]["Cases"] == "146"
    assert by_code[("wer:lassa_fever:confirmed:weekly:cases", "2026")]["Cases"] == "21"
    assert by_code[("wer:lassa_fever:all_reported:weekly:deaths", "2026")]["Cases"] == "1"
    assert by_code[("wer:lassa_fever:suspected:cumulative:cases", "2026")]["Cases"] == "7580"
    assert by_code[("wer:lassa_fever:suspected:cumulative:cases", "2025")]["Cases"] == "7253"
    assert by_code[("wer:cholera:confirmed:weekly:cases", "2026")]["Cases"] == "125"
    assert {r["ReportingBasis"] for r in rows if "influenza_sentinel" in r["SourceDiseaseCode"]} == {"sentinel_surveillance"}
    assert all(r["GeographyKey"] == "country:NG:national" for r in rows)


def test_source_missing_markers_are_unknown_and_observed_zeroes_remain_zero():
    pages = extract_pdf_pages((FIXTURES / "wer_2026_w34.pdf").read_bytes())
    pages[2] = pages[2].replace("146 21 1", "— 21 1")
    rows, _ = parse_weekly_pages(pages, REPORT)
    missing = next(r for r in rows if r["SourceDiseaseCode"] == "wer:lassa_fever:suspected:weekly:cases")
    assert (missing["Cases"], missing["MissingValueStatus"]) == ("", "source_missing")
    zero = next(r for r in rows if r["SourceDiseaseCode"] == "wer:mpox:confirmed:weekly:cases")
    assert (zero["Cases"], zero["MissingValueStatus"]) == ("0", "")


def test_table_drift_is_a_visible_disease_gap_in_a_partial_report():
    pages = extract_pdf_pages((FIXTURES / "wer_2026_w34.pdf").read_bytes())
    pages[2] = pages[2].replace("146 21 1", "146 2.1 1")
    rows, audit = parse_weekly_pages(pages, REPORT)
    assert not any(":lassa_fever:" in r["SourceDiseaseCode"] for r in rows)
    assert "lassa_fever" in audit["mentioned_unparsed_categories"]
    assert any(i.get("category") == "Lassa Fever" for i in audit["issues"])


def test_pdf_period_overrides_wrong_archive_route_and_is_audited():
    pages = extract_pdf_pages((FIXTURES / "wer_2026_w34.pdf").read_bytes())
    rows, audit = parse_weekly_pages(pages, REPORT | {"week": 35})
    assert {r["SourceReportWeek"] for r in rows} == {"34"}
    assert any(i["error"] == "Route/PDF reporting period differs" for i in audit["issues"])


def test_real_legacy_summary_does_not_invent_zero_for_omitted_counts():
    pages = json.loads((FIXTURES / "wer_2019_w01_pages.json").read_text())
    report = {"url": "https://ncdc.gov.ng/themes/common/docs/wers/169_1548415840.pdf", "kind": "weekly", "year": 2019, "week": 1}
    rows, audit = parse_weekly_pages(pages, report)
    assert len(rows) == 13
    afp = [r for r in rows if ":acute_flaccid_paralysis:" in r["SourceDiseaseCode"]]
    assert len(afp) == 1
    assert afp[0]["Cases"] == "51"
    assert afp[0]["SourceDiseaseCode"] == "wer:acute_flaccid_paralysis:reported:weekly:cases"
    assert "yellow_fever" in audit["mentioned_unparsed_categories"]


@pytest.mark.parametrize("category,observations,first_count", [(6, 6, "296"), (7, 4, "2"), (8, 6, "42"), (10, 6, "122"), (11, 9, "373"), (13, 4, "728"), (18, 12, "36"), (19, 6, "220")])
def test_real_situation_report_templates_preserve_known_national_counts(category, observations, first_count):
    pages = json.loads((FIXTURES / f"sitrep_category_{category}_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": category})
    assert len(rows) == observations
    assert rows[0]["Cases"] == first_count
    assert all(r["GeographyKey"] == "country:NG:national" for r in rows)
    if category == 6:
        seasonal = [r for r in rows if "cumulative_season" in r["SourceDiseaseCode"]]
        assert {r["SourcePeriodStart"] for r in seasonal} == {"2024-09-30"}
        assert {r["SourcePeriodEnd"] for r in seasonal} == {"2025-04-06"}
    if category == 11:
        assert rows[0]["Date"] == "2026-09-01"
        assert rows[0]["SourcePeriodEnd"] == "2026-09-30"
        assert rows[0]["Frequency"] == "monthly"
    if category == 19:
        # The separate Jan-2025 to Oct-2026 outbreak total is not 2026 YTD.
        assert "1271" not in {r["Cases"] for r in rows}


def test_real_legacy_merged_table_retains_all_counts_and_prior_year_without_assuming_case_classification():
    report = {"url": "https://ncdc.gov.ng/themes/common/docs/wers/7_1475852376.pdf", "kind": "weekly", "year": 2016, "week": 37}
    rows, audit = parse_report((FIXTURES / "wer_2016_w36_first_page.pdf").read_bytes(), report)
    assert len(rows) == 75
    assert len(audit["parsed_categories"]) == 7
    assert {r["SourcePeriodYear"] for r in rows} == {"2015", "2016"}
    lassa = [r for r in rows if r["SourceDiseaseCode"] == "wer:lassa_fever:idsr_reported:weekly:cases" and r["Date"] == "2016-09-11"]
    assert [r["Cases"] for r in lassa] == ["7"]
    assert "suspected" not in {json.loads(r["Dimensions"])["case_classification"] for r in rows}
    assert any(i["error"] == "Route/PDF reporting period differs" for i in audit["issues"])


def test_covid_explicit_daily_vectors_and_daily_cumulative_counts_remain_separate():
    pages = json.loads((FIXTURES / "covid_0_pages.json").read_text())
    report = {"url": "https://ncdc.gov.ng/covid.pdf", "kind": "sitrep", "category_id": 14}
    rows, _ = parse_sitrep_pages(pages, report)
    assert len(rows) == 46
    daily_cases = [r for r in rows if r["SourceDiseaseCode"] == "sitrep:covid_19:confirmed:daily:cases"]
    assert len(daily_cases) == 14
    assert sum(int(r["Cases"]) for r in daily_cases) == 26
    assert min(r["Date"] for r in daily_cases) == "2023-02-13"
    assert max(r["Date"] for r in daily_cases) == "2023-02-26"
    assert next(r["Cases"] for r in rows if r["SourceDiseaseCode"] == "sitrep:covid_19:confirmed:cumulative_daily:cases") == "266313"
    changed = [p.replace("Total 26 1 5 3", "Total 27 1 5 3") for p in pages]
    partial, audit = parse_sitrep_pages(changed, report)
    assert not any(r["SourceDiseaseCode"].endswith(":daily:cases") for r in partial)
    assert any("reconcile" in issue["error"] for issue in audit["issues"])
    assert next(r["Cases"] for r in partial if r["SourceDiseaseCode"].endswith(":cumulative_daily:cases")) == "266313"


@pytest.mark.parametrize("name,first_count,total", [("lassa_2024_w30", "111", 12), ("lassa_2021_w35", "74", 12), ("lassa_2021_w29", "66", 12), ("lassa_2020_w03", "159", 9)])
def test_authentic_lassa_reordered_compact_and_negative_tables(name, first_count, total):
    pages = json.loads((FIXTURES / f"{name}_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": 5})
    assert len(rows) == total
    assert rows[0]["Cases"] == first_count
    if name == "lassa_2020_w03":
        values = {(r["SourcePeriodYear"], r["SourceDiseaseCode"]): r["Cases"] for r in rows}
        assert values[("2020", "sitrep:lassa_fever:confirmed:cumulative:cases")] == "163"
        assert values[("2019", "sitrep:lassa_fever:confirmed:cumulative:deaths")] == "33"
        assert "78" not in {r["Cases"] for r in rows}


def test_authentic_mpox_reverse_pair_table_preserves_previous_full_year_endpoint():
    pages = json.loads((FIXTURES / "mpox_2024_w40_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": 8})
    assert len(rows) == 9
    assert rows[0]["Cases"] == "6"
    assert rows[1]["Cases"] == "47"
    previous = [r for r in rows if r["SourcePeriodYear"] == "2023"]
    assert {r["SourcePeriodWeek"] for r in previous} == {"52"}
    assert {r["Date"] for r in previous} == {"2023-12-31"}


def test_authentic_lassa_prose_decodes_counts_and_withholds_cross_year_outbreak_totals():
    report = {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": 5}
    pages = json.loads((FIXTURES / "lassa_2019_w02_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, report)
    values = {r["SourceDiseaseCode"]: r["Cases"] for r in rows}
    assert values == {"sitrep:lassa_fever:confirmed:weekly:cases": "35", "sitrep:lassa_fever:all_reported:weekly:deaths": "8", "sitrep:lassa_fever:suspected:cumulative:cases": "172", "sitrep:lassa_fever:confirmed:cumulative:cases": "60", "sitrep:lassa_fever:confirmed:cumulative:deaths": "16"}
    pages = json.loads((FIXTURES / "lassa_2017_w09_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, report)
    assert [(r["Cases"], r["Measure"]) for r in rows] == [("7", "cases"), ("4", "cases"), ("2", "deaths")]
    assert {r["PeriodType"] for r in rows} == {"weekly"}
    assert "247" not in {r["Cases"] for r in rows}


@pytest.mark.parametrize("name,when,tests", [("covid_2021_w08", "2021-02-28", "1580442"), ("covid_2020_w53", "2021-01-03", "975786"), ("covid_2020_10_10", "2020-10-10", None)])
def test_authentic_covid_header_order_cross_year_and_compact_text(name, when, tests):
    pages = json.loads((FIXTURES / f"{name}_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": 14})
    assert {r["Date"] for r in rows} == {when}
    if tests:
        assert next(r["Cases"] for r in rows if r["Measure"] == "tests") == tests
    if name == "covid_2020_w53":
        assert {r["SourceReportYear"] for r in rows} == {"2020"}
        assert {r["SourcePeriodYear"] for r in rows} == {"2020"}
        assert {r["SourceReportWeek"] for r in rows} == {"53"}
    if name == "covid_2020_10_10":
        assert next(r["Cases"] for r in rows if r["SourceDiseaseCode"].endswith(":daily:cases")) == "111"


def test_legacy_summary_unicode_bullets_do_not_leak_counts_between_diseases():
    pages = json.loads((FIXTURES / "wer_2019_w01_pages.json").read_text())
    report = {"url": "https://ncdc.gov.ng/report.pdf", "kind": "weekly", "year": 2019, "week": 1}
    baseline = parse_weekly_pages(pages, report)[0]
    for bullet in ["•", "▪", ""]:
        changed = [p.replace("o There", bullet + " There") for p in pages]
        assert parse_weekly_pages(changed, report)[0] == baseline


def test_authentic_mpox_multiweek_and_indicators_keep_actual_periods():
    report = {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": 8}
    pages = json.loads((FIXTURES / "mpox_2024_w25_28_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, report)
    assert len(rows) == 9
    assert [(r["Cases"], r["SourcePeriodStart"], r["SourcePeriodEnd"]) for r in rows[:3]] == [("102", "2024-06-17", "2024-07-14"), ("7", "2024-06-17", "2024-07-14"), ("0", "2024-06-17", "2024-07-14")]
    assert {r["Frequency"] for r in rows[:3]} == {"multiweek"}
    pages = json.loads((FIXTURES / "mpox_2022_w51_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, report)
    assert [r["Cases"] for r in rows] == ["3", "55", "2096", "755", "7"]
    assert not any(r["Cases"] == "15" for r in rows)  # since-2017 deaths are not January YTD


@pytest.mark.parametrize("name,category,expected", [("covid_2020_05_28", 14, ["8915", "2592", "259", "182", "91", "5"]), ("covid_2020_09_01", 14, ["54247", "42010", "1023", "239", "372", "10"]), ("cholera_2023_w09", 7, ["101", "2", "922", "32"]), ("yf_2018_w43", 10, ["89", "0"])])
def test_authentic_older_national_tables_and_weekly_prose(name, category, expected):
    pages = json.loads((FIXTURES / f"{name}_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/report.pdf", "kind": "sitrep", "category_id": category})
    assert [r["Cases"] for r in rows] == expected
    if name == "covid_2020_09_01":
        assert not any(r["Measure"] == "tests" for r in rows)  # collected samples are not tests
    if name == "yf_2018_w43":
        assert rows[1]["SourceDiseaseCode"] == "sitrep:yellow_fever:ip_dakar_confirmed:weekly:cases"
        assert not any(r["PeriodType"].startswith("cumulative") for r in rows)


def test_covid_real_daily_report_retains_source_zero_deaths():
    pages = json.loads((FIXTURES / "covid_150_pages.json").read_text())
    rows, _ = parse_sitrep_pages(pages, {"url": "https://ncdc.gov.ng/covid.pdf", "kind": "sitrep", "category_id": 14})
    assert len(rows) == 6
    assert {r["Date"] for r in rows} == {"2020-08-29"}
    assert next(r["Cases"] for r in rows if r["SourceDiseaseCode"] == "sitrep:covid_19:confirmed:daily:cases") == "250"
    assert next(r["Cases"] for r in rows if r["SourceDiseaseCode"] == "sitrep:covid_19:confirmed:daily:deaths") == "0"


def test_archive_discovery_keeps_all_categories_and_uses_only_official_downloads():
    listing = b'<a href="/reports/weekly/2016/9/37">old</a><a href="/reports/weekly/2026/8/34">new</a>'
    assert len(discover_weekly_pages(listing)) == 2
    assert len(discover_categories(b'<a href="/diseases/sitreps/?cat=5">Lassa</a><a href="/diseases/sitreps/?cat=99">new</a>')) == 2
    pdf = b'<a href="/themes/common/docs/wers/700_1791361960.pdf">PDF</a><a href="https://other.example/a.pdf">other</a>'
    assert len(discover_reports(pdf, listing_url=REPORT["listing_url"], kind="weekly")) == 1
    with pytest.raises(NigeriaNCDCContractError):
        discover_reports(b"<html>No download</html>", listing_url=REPORT["listing_url"], kind="weekly")
    with pytest.raises(NigeriaNCDCContractError):
        discover_categories(b"")


def test_same_report_conflicts_are_withheld_and_newer_comparative_revisions_win(monkeypatch):
    original = next(r for r in real_rows() if r["SourceDiseaseCode"] == "wer:lassa_fever:suspected:cumulative:cases" and r["SourcePeriodYear"] == "2025")
    changed = dict(original, Cases="7254", SourceURL="https://ncdc.gov.ng/other.pdf")
    monkeypatch.setattr(ng, "parse_report", lambda payload, report: ([original if payload == b"original" else changed], {}))
    rows, audit = ng.organize_documents([(REPORT, b"original"), (REPORT, b"changed")])
    assert not rows
    assert len(audit["conflicting_observations"]) == 1
    changed["SourceReportYear"] = "2027"
    rows, audit = ng.organize_documents([(REPORT, b"original"), (REPORT, b"changed")])
    assert [r["Cases"] for r in rows] == ["7254"]
    assert not audit["conflicting_observations"]


def test_offline_staged_organization_checks_raw_integrity_and_never_contacts_source(tmp_path, monkeypatch):
    payload = (FIXTURES / "wer_2026_w34.pdf").read_bytes()
    monkeypatch.chdir(tmp_path)
    directory = Path("data/raw/ng/test")
    directory.mkdir(parents=True)
    def artifact(name, kind, payload, metadata=None):
        path = directory / name
        path.write_bytes(payload)
        return SimpleNamespace(kind=kind, relative_path=path.as_posix(), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), metadata_=metadata or {})
    artifacts = [artifact("report.pdf", "ng_ncdc_pdf", payload, {"report": REPORT}), artifact("plan.json", "ng_ncdc_plan", json.dumps({"reports": [REPORT], "mode": "test_offline"}).encode())]
    monkeypatch.setattr(requests.Session, "get", lambda *a, **k: pytest.fail("Organization made an HTTP request"))
    fetched = ng.organize(artifacts, updater=NigeriaNCDCUpdater(), source=DEFAULT_SCOPE)
    assert len(fetched.rows) == 85
    assert fetched.source_audit["parsed_reports"] == 1
    (directory / "report.pdf").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        ng.organize(artifacts, updater=NigeriaNCDCUpdater())
