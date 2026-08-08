from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest

from src.data.crawlers.no import (
    DEFAULT_SOURCE_NAME,
    MONTH_NAMES,
    NOContractError,
    NorwayMSISCrawler,
    validate_diagnosis_catalog,
    validate_monthly_payload,
)
from src.data.processors.no import NOMonthlyUpdater


def _catalog_payload():
    return [
        {
            "id": 1,
            "nr": 1,
            "beskrivelse": "Vaksinesykdommer",
            "fra": "100",
            "til": "199",
            "diagnoseListe": [
                {"id": 9, "verdi": "108", "beskrivelse": "Difteri"},
                {"id": 24, "verdi": "101", "beskrivelse": "Kikhoste"},
            ],
        }
    ]


def _monthly_payload(labels=("Difteri", "Kikhoste")):
    norwegian_months = list(MONTH_NAMES)
    rows = []
    for month_index, month_name in enumerate(norwegian_months, start=1):
        for label in labels:
            rows.append(
                {
                    "tekst": label,
                    "antall": 0 if label == "Difteri" else month_index,
                    "fordeltPaa": month_name.title(),
                }
            )
    return rows


class _FakeResponse:
    def __init__(self, payload, url):
        self._payload = payload
        self.url = url
        self.status_code = 200
        self.headers = {"Content-Type": "application/json; charset=utf-8"}
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def json(self):
        return self._payload


def _install_fake_api(monkeypatch, crawler):
    def fake_get(url, **kwargs):
        params = kwargs.get("params") or []
        request_url = f"{url}?{urlencode(params)}" if params else url
        if url.endswith("/kodeverk/diagnoser"):
            return _FakeResponse(_catalog_payload(), request_url)
        if url.endswith("/etterDiagnoseFordeltPaaMaaned"):
            return _FakeResponse(_monthly_payload(), request_url)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(crawler, "get", fake_get)


def test_diagnosis_catalog_contract_is_flattened_with_source_codes():
    diagnoses = validate_diagnosis_catalog(_catalog_payload())

    assert [(item.code, item.name) for item in diagnoses] == [
        ("108", "Difteri"),
        ("101", "Kikhoste"),
    ]
    assert all(item.group_name == "Vaksinesykdommer" for item in diagnoses)


def test_diagnosis_catalog_contract_rejects_silent_schema_drift():
    payload = _catalog_payload()
    payload[0]["diagnoseListe"][0]["verdi"] = None

    with pytest.raises(NOContractError, match="empty code/name"):
        validate_diagnosis_catalog(payload)


def test_monthly_contract_requires_one_row_per_norwegian_month():
    diagnoses = validate_diagnosis_catalog(_catalog_payload())
    payload = _monthly_payload()
    payload.pop()

    with pytest.raises(NOContractError, match="all 12 months"):
        validate_monthly_payload(payload, selected_diagnoses=diagnoses)


def test_crawler_drops_open_and_future_placeholders_but_keeps_closed_zeroes(
    tmp_path, monkeypatch
):
    crawler = NorwayMSISCrawler(delay=0)
    _install_fake_api(monkeypatch, crawler)
    output = tmp_path / "norway.csv"

    summary = crawler.crawl_monthly_national(
        output,
        months=[(2026, month) for month in range(1, 10)],
        as_of=date(2026, 8, 7),
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert summary.row_count == 14
    assert {row["Date"] for row in rows} == {
        f"2026-{month:02d}-01" for month in range(1, 8)
    }
    assert all(row["DataStatus"] == "closed" for row in rows)
    assert any(
        row["RawDiseaseLabel"] == "Difteri"
        and row["Date"] == "2026-07-01"
        and row["Cases"] == "0"
        for row in rows
    )


def test_crawler_current_month_is_explicitly_provisional(tmp_path, monkeypatch):
    crawler = NorwayMSISCrawler(delay=0)
    _install_fake_api(monkeypatch, crawler)
    output = tmp_path / "norway.csv"

    crawler.crawl_monthly_national(
        output,
        months=[(2026, 8), (2026, 9)],
        as_of=date(2026, 8, 7),
        include_current_month=True,
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert {row["Date"] for row in rows} == {"2026-08-01"}
    assert {row["DataStatus"] for row in rows} == {"provisional"}
    assert {row["AuthoritativeRevision"] for row in rows} == {"true"}
    assert {row["UpdateMode"] for row in rows} == {"dynamic_provisional"}


def test_raw_provenance_archives_catalog_and_exact_year_response(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    crawler = NorwayMSISCrawler(save_raw=True, raw_dir=raw_dir, delay=0)
    _install_fake_api(monkeypatch, crawler)
    output = tmp_path / "norway.csv"

    crawler.crawl_monthly_national(
        output,
        months=[(2025, 1)],
        as_of=date(2026, 8, 7),
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    artifacts = list(raw_dir.rglob("*.json"))

    assert len(artifacts) == 2
    assert rows[0]["RawArtifact"]
    assert Path(rows[0]["RawArtifact"]).exists()
    assert len(rows[0]["RawSHA256"]) == 64
    archived = json.loads(Path(rows[0]["RawArtifact"]).read_text(encoding="utf-8"))
    assert archived["contract_version"].startswith("fhi-msis-allvis-v1")
    assert archived["request"]["params"][0:2] == [
        ["fraAar", "2025"],
        ["tilAar", "2025"],
    ]


def test_refresh_merge_replaces_revised_target_month_and_preserves_others(
    tmp_path, monkeypatch
):
    output = tmp_path / "norway.csv"
    output.write_text(
        "Date,RawDiseaseLabel,DiseaseCode,DiseaseGroup,Year,Month,Cases,Deaths,"
        "ReportingArea,DataStatus,Source,SourceScope,SourceURL,RetrievedAt,"
        "SourceContract,RawArtifact,RawSHA256\n"
        "2026-06-01,Old June,999,Group,2026,6,4,,Norway national,closed,"
        f"{DEFAULT_SOURCE_NAME},fhi_msis,https://example.test,,,,\n"
        "2026-07-01,Stale July,998,Group,2026,7,99,,Norway national,closed,"
        f"{DEFAULT_SOURCE_NAME},fhi_msis,https://example.test,,,,\n",
        encoding="utf-8",
    )
    crawler = NorwayMSISCrawler(delay=0)
    _install_fake_api(monkeypatch, crawler)

    crawler.crawl_monthly_national(
        output,
        months=[(2026, 7)],
        as_of=date(2026, 8, 7),
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert any(row["RawDiseaseLabel"] == "Old June" for row in rows)
    assert not any(row["RawDiseaseLabel"] == "Stale July" for row in rows)
    assert sum(row["Date"] == "2026-07-01" for row in rows) == 2


def test_updater_recent_window_uses_three_closed_months_across_year_boundary(
    tmp_path,
):
    updater = NOMonthlyUpdater(
        output_csv=tmp_path / "norway.csv",
        today_provider=lambda: date(2026, 1, 10),
    )

    assert updater._default_recent_months() == [
        (2025, 10),
        (2025, 11),
        (2025, 12),
    ]


def test_updater_history_defaults_to_1977_and_last_closed_month(tmp_path):
    updater = NOMonthlyUpdater(
        output_csv=tmp_path / "norway.csv",
        today_provider=lambda: date(2026, 8, 7),
    )

    months = updater.history_months()

    assert months[0] == (1977, 1)
    assert months[-1] == (2026, 7)
    assert len(months) == 595


def test_no_mapping_rows_have_stable_source_and_unique_series_ids():
    mapping_path = Path(__file__).resolve().parents[1] / "configs/mapping/no.csv"
    with mapping_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) >= 60
    assert {row["data_source"] for row in rows} == {
        "Norway FHI MSIS Statistics Bank"
    }
    assert {row["source_id"] for row in rows} == {"SRC_NO_FHI_MSIS"}
    assert len({row["series_id"] for row in rows}) == len(rows)


@pytest.mark.network
def test_live_fhi_msis_small_closed_month_fetch(tmp_path):
    crawler = NorwayMSISCrawler(save_raw=True, raw_dir=tmp_path / "raw", delay=0)
    output = tmp_path / "norway.csv"

    summary = crawler.crawl_monthly_national(
        output,
        months=[(2026, 6), (2026, 7)],
        as_of=date(2026, 8, 7),
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert summary.row_count > 0
    assert {row["Date"] for row in rows} == {"2026-06-01", "2026-07-01"}
    assert all(row["Source"] == DEFAULT_SOURCE_NAME for row in rows)
    assert all(row["DataStatus"] == "closed" for row in rows)
