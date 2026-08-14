from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import httpx
import pytest

from src.data.crawlers.se import (
    DEFAULT_SOURCE_NAME,
    ONTOLOGY_SOURCE_ID,
    SEDiseasePage,
    SwedenSmiNetCrawler,
    closed_months,
    parse_monthly_csv,
    parse_monthly_html,
)
from src.data.processors.se import SEMonthlyUpdater


FIXTURES = Path(__file__).parent / "fixtures/se"
PAGE_URL = (
    "https://www.folkhalsomyndigheten.se/statistik-och-data/"
    "hitta-statistik-och-data/gonorre-statistik/"
)
CSV_URL = (
    "https://sminet3-prod.sminet.se/mapapp/"
    "gonorre_mSWE_2025_ALL_20260808.csv"
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _gonorrhea_page() -> SEDiseasePage:
    return SEDiseasePage(code="gonorre", index_label="Gonorré", url=PAGE_URL)


def _page(code: str) -> SEDiseasePage:
    return SEDiseasePage(
        code=code,
        index_label=code.title(),
        url=PAGE_URL.replace("gonorre", code),
    )


def _current_year_html(*, august_cases: int) -> str:
    return (
        _fixture("gonorre_2025.html")
        .replace("2025", "2026")
        .replace(
            '<strong class="total">425</strong>',
            f'<strong class="total">{august_cases}</strong>',
        )
    )


def test_se_index_discovery_deduplicates_aliases_and_restricts_host():
    crawler = SwedenSmiNetCrawler(delay=0)
    try:
        pages = crawler.discover_disease_pages(_fixture("fhm_index.html"))
    finally:
        crawler.close()

    assert [(page.code, page.index_label) for page in pages] == [
        ("alkohol", "Alkohol"),
        ("gonorre", "Gonorré"),
    ]


def test_se_html_parser_uses_national_total_count_not_rate():
    totals = parse_monthly_html(_fixture("gonorre_2025.html"), 2025)

    assert totals[1] == 431
    assert totals[2] == 398
    assert totals[7] == 440
    assert totals[1] != 4


def test_se_machine_csv_parser_uses_total_count_not_decimal_rate():
    totals = parse_monthly_csv((FIXTURES / "gonorre_2025.csv").read_bytes())

    assert totals[1] == 431
    assert totals[2] == 398
    assert totals[12] == 399


def test_se_closed_month_filter_excludes_current_and_future_months():
    assert closed_months(count=3, today=date(2026, 8, 7)) == [
        (2026, 4),
        (2026, 5),
        (2026, 6),
    ]
    assert closed_months(
        [(2025, 12), (2026, 7), (2026, 8), (2026, 9)],
        today=date(2026, 8, 7),
    ) == [(2025, 12)]

    assert closed_months(
        [(2026, 6), (2026, 7), (2026, 8)],
        today=date(2026, 8, 8),
    ) == [(2026, 6), (2026, 7)]

    assert closed_months(
        [(2026, 6), (2026, 7), (2026, 8), (2026, 9)],
        today=date(2026, 8, 7),
        include_current_month=True,
    ) == [(2026, 6), (2026, 7), (2026, 8)]
    assert closed_months(
        count=3,
        today=date(2026, 8, 7),
        include_current_month=True,
    ) == [
        (2026, 6),
        (2026, 7),
        (2026, 8),
    ]


def test_se_current_month_is_retained_for_all_series_after_nonzero_source_evidence(
    tmp_path, monkeypatch
):
    pages = [_page("alpha"), _page("beta")]
    crawler = SwedenSmiNetCrawler(delay=0)
    monkeypatch.setattr(crawler, "discover_disease_pages", lambda: pages)
    monkeypatch.setattr(
        crawler,
        "_fetch_page_html",
        lambda page, _year: (
            _current_year_html(august_cases=7 if page.code == "beta" else 0),
            page.url,
        ),
    )
    monkeypatch.setattr(crawler, "_fetch_csv_bytes", lambda _url: b"")
    output = tmp_path / "se-current.csv"
    try:
        summary = crawler.crawl_monthly_national(
            output,
            months=[(2026, 8), (2026, 9)],
            today=date(2026, 8, 7),
            include_current_month=True,
        )
    finally:
        crawler.close()

    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert [(row["DiseaseCode"], row["Cases"]) for row in rows] == [
        ("alpha", "0"),
        ("beta", "7"),
    ]
    assert {row["Date"] for row in rows} == {"2026-08-01"}
    assert {row["DatasetStatus"] for row in rows} == {"provisional"}
    assert {row["IsProvisional"] for row in rows} == {"true"}
    assert {row["DataComplete"] for row in rows} == {"false"}
    assert {row["AuthoritativeRevision"] for row in rows} == {"true"}
    assert summary.latest_date == date(2026, 8, 1)
    assert summary.placeholder_months_omitted == ()


def test_se_all_zero_open_month_is_omitted_as_one_source_wide_placeholder(
    tmp_path, monkeypatch
):
    pages = [_page("alpha"), _page("beta")]
    crawler = SwedenSmiNetCrawler(delay=0)
    monkeypatch.setattr(crawler, "discover_disease_pages", lambda: pages)
    monkeypatch.setattr(
        crawler,
        "_fetch_page_html",
        lambda page, _year: (_current_year_html(august_cases=0), page.url),
    )
    monkeypatch.setattr(crawler, "_fetch_csv_bytes", lambda _url: b"")
    output = tmp_path / "se-placeholder.csv"
    try:
        summary = crawler.crawl_monthly_national(
            output,
            months=[(2026, 6), (2026, 8), (2026, 9)],
            today=date(2026, 8, 7),
            include_current_month=True,
        )
    finally:
        crawler.close()

    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert len(rows) == 2
    assert {row["Date"] for row in rows} == {"2026-06-01"}
    assert {row["DatasetStatus"] for row in rows} == {"closed_revisable"}
    assert {row["IsProvisional"] for row in rows} == {"false"}
    assert summary.latest_date == date(2026, 6, 1)
    assert summary.placeholder_months_omitted == ((2026, 8),)


def test_se_crawler_prefers_machine_csv_and_retains_provenance(tmp_path, monkeypatch):
    crawler = SwedenSmiNetCrawler(delay=0)
    monkeypatch.setattr(crawler, "discover_disease_pages", lambda: [_gonorrhea_page()])
    monkeypatch.setattr(
        crawler,
        "_fetch_page_html",
        lambda _page, _year: (_fixture("gonorre_2025.html"), PAGE_URL + "?year=2025"),
    )
    monkeypatch.setattr(
        crawler,
        "_fetch_csv_bytes",
        lambda _url: (FIXTURES / "gonorre_2025.csv").read_bytes(),
    )
    output = tmp_path / "se.csv"
    try:
        summary = crawler.crawl_monthly_national(
            output,
            months=[(2025, 1)],
            today=date(2026, 8, 7),
        )
    finally:
        crawler.close()

    row = next(csv.DictReader(output.open(encoding="utf-8")))
    assert summary.row_count == 1
    assert summary.csv_pages == 1
    assert row["Cases"] == "431"
    assert row["RetrievalMethod"] == "machine_csv"
    assert row["SourceURL"] == PAGE_URL + "?year=2025"
    assert row["DownloadURL"] == CSV_URL
    assert row["SourceUpdatedAt"] == "2026-08-08"
    assert row["AuthoritativeRevision"] == "true"
    assert row["PublicReleaseEnabled"] == "true"


def test_se_csv_tls_failure_falls_back_to_html_and_caches_failed_host(
    tmp_path, monkeypatch
):
    class FailingCSVClient:
        def __init__(self):
            self.calls = 0

        def get(self, url, **_kwargs):
            self.calls += 1
            raise httpx.ConnectError(
                "[SSL: UNEXPECTED_EOF_WHILE_READING]",
                request=httpx.Request("GET", url),
            )

    client = FailingCSVClient()
    crawler = SwedenSmiNetCrawler(delay=0, http_client=client)  # type: ignore[arg-type]
    monkeypatch.setattr(crawler, "discover_disease_pages", lambda: [_gonorrhea_page()])
    monkeypatch.setattr(
        crawler,
        "_fetch_page_html",
        lambda _page, _year: (_fixture("gonorre_2025.html"), PAGE_URL + "?year=2025"),
    )
    output = tmp_path / "se.csv"
    try:
        summary = crawler.crawl_monthly_national(
            output,
            months=[(2025, 1)],
            today=date(2026, 8, 7),
        )
        with pytest.raises(RuntimeError, match="disabled after transport failure"):
            crawler._fetch_csv_bytes(CSV_URL)
    finally:
        crawler.close()

    row = next(csv.DictReader(output.open(encoding="utf-8")))
    assert client.calls == 1
    assert summary.csv_pages == 0
    assert summary.html_fallback_pages == 1
    assert row["Cases"] == "431"
    assert row["RetrievalMethod"] == "html_fallback"


def test_se_probe_year_filters_non_sminet_pages_before_history(tmp_path, monkeypatch):
    gonorrhea = _gonorrhea_page()
    alcohol = SEDiseasePage(code="alkohol", index_label="Alkohol", url=PAGE_URL.replace("gonorre", "alkohol"))
    crawler = SwedenSmiNetCrawler(delay=0)
    monkeypatch.setattr(crawler, "discover_disease_pages", lambda: [gonorrhea, alcohol])
    calls = []

    def fake_page(page, year):
        calls.append((page.code, year))
        if page.code == "alkohol":
            return "<html><h1>Alkohol – statistik</h1></html>", page.url
        return _fixture("gonorre_2025.html").replace("2025", str(year)), page.url

    monkeypatch.setattr(crawler, "_fetch_page_html", fake_page)
    monkeypatch.setattr(
        crawler,
        "_fetch_csv_bytes",
        lambda _url: (FIXTURES / "gonorre_2025.csv").read_bytes(),
    )
    try:
        summary = crawler.crawl_monthly_national(
            tmp_path / "se.csv",
            months=[(2024, 1), (2025, 1)],
            today=date(2026, 8, 7),
        )
    finally:
        crawler.close()

    assert calls == [("gonorre", 2025), ("gonorre", 2024), ("alkohol", 2025)]
    assert summary.pages_inspected == 3
    assert summary.row_count == 2


def test_se_updater_adds_revision_window_and_keeps_public_release_enabled(tmp_path):
    updater = SEMonthlyUpdater(output_csv=tmp_path / "se.csv")

    assert updater._resolve_requested_months(
        [(2024, 1), (2026, 8), (2026, 9)],
        today=date(2026, 8, 7),
    ) == [(2024, 1), (2026, 4), (2026, 5), (2026, 6)]
    assert updater._resolve_requested_months(
        [(2024, 1), (2026, 8), (2026, 9)],
        today=date(2026, 8, 7),
        include_current_month=True,
    ) == [
        (2024, 1),
        (2026, 6),
        (2026, 7),
        (2026, 8),
    ]
    assert updater.country_code == "SE"
    assert updater.source_scope == "fohm_sminet"
    assert updater.ontology_source_id == ONTOLOGY_SOURCE_ID
    assert updater.public_release_enabled is True
    assert callable(updater.refresh_source)
    assert callable(updater.get_db_latest_date)
    assert callable(updater.get_db_months)
    assert callable(updater.import_rows)


def test_se_catalog_rescan_is_due_weekly_and_survives_process_restart(tmp_path):
    state = tmp_path / "catalog_scan.json"
    updater = SEMonthlyUpdater(
        output_csv=tmp_path / "se.csv",
        catalog_rescan_interval_days=7,
        catalog_scan_state=state,
    )

    assert updater._catalog_rescan_due(today=date(2026, 8, 1), force=False)
    updater._record_catalog_scan(today=date(2026, 8, 1), disease_count=52)

    restarted = SEMonthlyUpdater(
        output_csv=tmp_path / "se.csv",
        catalog_rescan_interval_days=7,
        catalog_scan_state=state,
    )
    assert not restarted._catalog_rescan_due(today=date(2026, 8, 7), force=False)
    assert restarted._catalog_rescan_due(today=date(2026, 8, 8), force=False)
    assert restarted._catalog_rescan_due(today=date(2026, 8, 2), force=True)


def test_se_updater_load_rows_uses_reviewed_public_release_gate(tmp_path):
    output = tmp_path / "se.csv"
    output.write_text(
        "Date,RawDiseaseLabel,DiseaseCode,Cases,Source,SourceURL,DownloadURL,"
        "SourceUpdatedAt,RetrievalMethod,DatasetStatus,IsProvisional,DataComplete,"
        "PublicReleaseEnabled\n"
        "2025-01-01,Gonorré,gonorre,431,Sweden Public Health Agency SmiNet,"
        "https://example.test/page,https://example.test/file.csv,2026-08-08,"
        "html_fallback,provisional,true,false,true\n",
        encoding="utf-8",
    )

    row = SEMonthlyUpdater(output_csv=output)._load_rows(output)[0]

    assert row["Source"] == DEFAULT_SOURCE_NAME
    assert row["AuthoritativeRevision"] == "true"
    assert row["UpdateMode"] == "dynamic_provisional"
    assert row["DatasetStatus"] == "provisional"
    assert row["IsProvisional"] == "true"
    assert row["DataComplete"] == "false"
    assert row["PublicReleaseEnabled"] == "true"
    assert row["LicenseReviewStatus"] == "approved_for_public_release"


def test_se_mapping_is_source_scoped_and_leaves_ambiguous_categories_unmapped():
    mapping_path = Path(__file__).parents[1] / "configs/mapping/se.csv"
    rows = list(csv.DictReader(mapping_path.open(encoding="utf-8")))
    labels = {row["local_name"] for row in rows}
    mapped_rows = [row for row in rows if row["series_id"]]
    unmapped_labels = {row["local_name"] for row in rows if not row["series_id"]}

    assert len(rows) == 52
    assert len({row["series_id"] for row in mapped_rows}) == len(mapped_rows)
    assert {row["source_id"] for row in rows} == {ONTOLOGY_SOURCE_ID}
    assert {row["data_source"] for row in rows} == {DEFAULT_SOURCE_NAME}
    assert "Gonorré" in labels
    assert "HTLV 1- och HTLV 2-infektion" in labels
    assert "Meticillinresistenta gula stafylokocker" in labels
    assert "ESBL-CARBA" in labels
    assert unmapped_labels == {
        "Extended Spectrum Beta-Lactamase",
        "Penicillinresistenta pneumokocker",
        "Viral meningoencefalit",
    }
