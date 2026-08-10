from __future__ import annotations

from datetime import date

import pytest

from src.data.crawlers.ie import IEContractError
from src.data.crawlers.ie_weekly_archive import (
    IEWeeklyArchiveReport,
    parse_catalogue_item,
    parse_weekly_archive_pdf,
    validate_archive_rows,
)
from src.ontology import load_disease_ontology


def _catalogue_item(*, rights: bool = False):
    metadata = {}
    if rights:
        metadata["dc.rights.uri"] = [
            {"value": "https://creativecommons.org/licenses/by/4.0/"}
        ]
    return {
        "id": "item-20",
        "handle": "10147/638115",
        "name": (
            "Statutory Notifications of Infectious Diseases reported in Ireland "
            "via the Computerised Infectious Disease Reporting (CIDR) system for: "
            "Week 20, 2020 (Notification Period: 10/05/2020 - 16/05/2020)"
        ),
        "metadata": metadata,
    }


def test_catalogue_item_does_not_require_licence_for_internal_ingestion():
    report = parse_catalogue_item(_catalogue_item(rights=False))
    assert report is not None
    assert (report.year, report.week) == (2020, 20)
    assert report.rights_uri == ""


def test_catalogue_item_retains_declared_cc_by_metadata():
    report = parse_catalogue_item(_catalogue_item(rights=True))
    assert report is not None
    assert report.rights_uri == "https://creativecommons.org/licenses/by/4.0/"


def test_weekly_pdf_parser_uses_only_week_ending_column(monkeypatch):
    table_rows = [
        ["Infectious Disease", "Week\nEnding", "2020", "2019", "Increase/\nDecrease"],
        [None, "5/16/2020", "Week 1 - 20", "Week 1 - 20", "+/-"],
    ]
    table_rows.extend(
        [[f"Disease {index}", str(index % 4), "999", "888", "111"] for index in range(70)]
    )
    table_rows.append(["Total", str(sum(index % 4 for index in range(70))), "", "", ""])

    class Page:
        def extract_text(self):
            return "HPSC - Weekly Infectious Disease Report\nData are Provisional"

        def extract_tables(self):
            return [table_rows]

    class Document:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "src.data.crawlers.ie_weekly_archive.pdfplumber.open",
        lambda *_args, **_kwargs: Document(),
    )
    report = IEWeeklyArchiveReport(
        year=2020,
        week=20,
        period_start=date(2020, 5, 10),
        period_end=date(2020, 5, 16),
        item_id="fixture",
        handle="",
        title="fixture",
        rights_uri="",
        source_url="https://example.test/report.pdf",
        archive_provider="internet_archive",
    )
    rows = parse_weekly_archive_pdf(
        b"%PDF-fixture", report=report, retrieved_at="2026-08-10T00:00:00Z"
    )
    assert len(rows) == 70
    assert {row["Cases"] for row in rows} == {"0", "1", "2", "3"}
    assert all(row["PublicReleaseEnabled"] == "false" for row in rows)
    assert all(row["DatasetStatus"] == "historical_provisional_snapshot" for row in rows)
    validate_archive_rows(rows, requested_periods={(2020, 20)})


def test_weekly_pdf_parser_rejects_total_mismatch(monkeypatch):
    table = [
        ["Infectious Disease", "Week\nEnding", "2020", "2019", "Increase/\nDecrease"],
        [None, "16/05/2020", "Week 1 - 20", "Week 1 - 20", "+/-"],
        *[[f"Disease {index}", "1", "1", "0", "1"] for index in range(70)],
        ["Total", "1", "", "", ""],
    ]

    class Page:
        def extract_text(self):
            return "Data are Provisional"

        def extract_tables(self):
            return [table]

    class Document:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "src.data.crawlers.ie_weekly_archive.pdfplumber.open",
        lambda *_args, **_kwargs: Document(),
    )
    report = IEWeeklyArchiveReport(
        2020, 20, date(2020, 5, 10), date(2020, 5, 16),
        "fixture", "", "fixture", "",
    )
    with pytest.raises(IEContractError, match="total mismatch"):
        parse_weekly_archive_pdf(b"%PDF-fixture", report=report, retrieved_at="now")


def test_legacy_hpsc_week_label_is_normalized_from_notification_period(monkeypatch):
    table = [
        ["Infectious Disease", "Week\nEnding", "2015", "2014", "Increase/\nDecrease"],
        [None, "02/05/2015", "Week 1 - 17", "Week 1 - 17", "+/-"],
        *[[f"Disease {index}", "1", "1", "0", "1"] for index in range(68)],
        ["Total", "68", "", "", ""],
    ]

    class Page:
        def extract_text(self):
            return "Data are Provisional"

        def extract_tables(self):
            return [table]

    class Document:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "src.data.crawlers.ie_weekly_archive.pdfplumber.open",
        lambda *_args, **_kwargs: Document(),
    )
    report = IEWeeklyArchiveReport(
        2015, 17, date(2015, 4, 26), date(2015, 5, 2),
        "fixture", "", "fixture", "",
    )
    rows = parse_weekly_archive_pdf(b"%PDF-fixture", report=report, retrieved_at="now")
    assert len(rows) == 68
    assert {row["Date"] for row in rows} == {"2015-04-27"}
    assert {row["YearWeek"] for row in rows} == {"2015 W18"}
    assert {row["SourceReport"] for row in rows} == {"2015-W17"}
    validate_archive_rows(rows, requested_periods={(2015, 17)})


def test_archive_has_independent_weekly_registry_series():
    ontology = load_disease_ontology()
    rows = ontology.series_lookup(source_id="SRC_IE_HPSC_WEEKLY_ARCHIVE")
    assert len(rows) == 82
    assert all(row["frequency"] == "weekly" for row in rows)
    assert all(row["status"] == "historical" for row in rows)
    assert all(row["valid_from"] == "2015-04-27" for row in rows)
    assert all(row["valid_to"] == "2021-07-25" for row in rows)
