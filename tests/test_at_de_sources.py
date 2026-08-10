from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from src.data.crawlers.at import AGESIssue, parse_ages_csv
from src.data.crawlers.de import parse_survstat_zip


def test_ages_parser_uses_month_column_not_ytd() -> None:
    issue = AGESIssue(
        detail_url="https://example.test/issue",
        csv_url="https://example.test/issue.csv",
        report_month=datetime(2026, 2, 1).date(),
        retrieved_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    rows = parse_ages_csv(
        '"Krankheit";"Feb 2026";"Jan - Feb 2026";"Jan - Feb 2025"\n"Campylobacteriose";"7";"11";"8"\n'.encode(),
        issue=issue,
    )
    assert [(row["Date"], row["Cases"], row["DiseaseCode"]) for row in rows] == [("2026-02-01", "7", "__source_native__")]
    assert json.loads(rows[0]["Dimensions"])["source_disease_code"] == "campylobacteriose"
    assert rows[0]["PublicReleaseEnabled"] == "false"


def test_ages_parser_accepts_live_german_month_and_two_digit_year() -> None:
    issue = AGESIssue(
        detail_url="https://example.test/issue",
        csv_url="https://example.test/issue.csv",
        report_month=datetime(2026, 6, 1).date(),
        retrieved_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    rows = parse_ages_csv(
        '"Krankheit";"Jun 26";"Jan - Jun 2026"\n"Masern";"3";"8"\n'.encode(),
        issue=issue,
    )
    assert rows[0]["Date"] == "2026-06-01"
    assert rows[0]["Cases"] == "3"


def test_survstat_zip_parser_retains_week_and_source_category() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("export.csv", "Krankheit;Meldejahr;Meldewoche;Anzahl Fälle\nCampylobacteriose;2026;31;12\n")
    rows = parse_survstat_zip(stream.getvalue(), source_url="https://example.test/export", retrieved_at=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert [(row["Date"], row["Cases"], row["DiseaseCode"]) for row in rows] == [("2026-07-27", "12", "__source_native__")]
    assert json.loads(rows[0]["Dimensions"])["source_disease_label"] == "Campylobacteriose"


def test_survstat_zip_parser_accepts_live_utf16_pivot_export() -> None:
    stream = io.BytesIO()
    pivot = '"Krankheit"\t"Meldewoche"\r\n""\t"01"\t"02"\r\n"Masern"\t"2"\t"3"\r\n'
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("Data.csv", pivot.encode("utf-16"))
    rows = parse_survstat_zip(stream.getvalue(), source_url="https://example.test/export", retrieved_at=datetime(2026, 8, 10, tzinfo=timezone.utc), export_year=2026)
    assert [(row["Week"], row["Cases"]) for row in rows] == [("1", "2"), ("2", "3")]
