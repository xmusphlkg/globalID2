from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from src.data.crawlers.br import (
    BrazilSINANCrawler,
    SINANFile,
    BRFetchSummary,
    parse_ftp_listing,
)
from src.data.processors.br import BRMonthlyUpdater


def _write_test_dbf(path: Path, records: list[dict[str, str]]) -> None:
    fields = [
        ("ID_AGRAVO", "C", 5),
        ("DT_NOTIFIC", "D", 8),
        ("NU_ANO", "C", 4),
    ]
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(length for _name, _typ, length in fields)

    payload = bytearray()
    payload.extend(
        bytes(
            [
                0x03,
                126,
                5,
                19,
            ]
        )
    )
    payload.extend(len(records).to_bytes(4, "little"))
    payload.extend(header_len.to_bytes(2, "little"))
    payload.extend(record_len.to_bytes(2, "little"))
    payload.extend(bytes(20))

    for name, field_type, length in fields:
        descriptor = bytearray(32)
        descriptor[: len(name)] = name.encode("ascii")
        descriptor[11] = ord(field_type)
        descriptor[16] = length
        payload.extend(descriptor)
    payload.append(0x0D)

    for record in records:
        payload.extend(b" ")
        for name, field_type, length in fields:
            raw = record.get(name, "")
            encoded = raw.encode("ascii")
            if field_type == "N":
                payload.extend(encoded.rjust(length, b" ")[:length])
            else:
                payload.extend(encoded.ljust(length, b" ")[:length])
    payload.append(0x1A)
    path.write_bytes(payload)


def test_parse_sinan_ftp_listing_prefers_file_metadata() -> None:
    listing = "05-12-26  03:32PM             11193039 DENGBR26.dbc\n"
    files = parse_ftp_listing(
        listing,
        base_url="ftp://ftp.datasus.gov.br/dissemin/publicos/SINAN/DADOS/PRELIM/",
        dataset_status="preliminary",
    )

    assert len(files) == 1
    assert files[0].prefix == "DENG"
    assert files[0].year == 2026
    assert files[0].disease_name == "Dengue"
    assert files[0].size_bytes == 11193039
    assert files[0].url.endswith("/DENGBR26.dbc")


def test_aggregate_file_counts_notification_months(tmp_path, monkeypatch) -> None:
    fake_dbf = tmp_path / "BOTUBR26.dbf"
    fake_dbc = tmp_path / "BOTUBR26.dbc"
    fake_dbc.write_bytes(b"fake")
    _write_test_dbf(
        fake_dbf,
        [
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260105", "NU_ANO": "2026"},
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260120", "NU_ANO": "2026"},
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260201", "NU_ANO": "2026"},
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260301", "NU_ANO": "2026"},
        ],
    )

    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=tmp_path / "raw")
    monkeypatch.setattr(crawler, "_download_file", lambda _item, _target_dir: fake_dbc)
    monkeypatch.setattr(
        BrazilSINANCrawler,
        "_decompress_to_dbf",
        staticmethod(lambda _dbc, dbf: shutil.copyfile(fake_dbf, dbf)),
    )

    rows = crawler.aggregate_file(
        SINANFile(
            prefix="BOTU",
            disease_name="Botulism",
            year=2026,
            filename="BOTUBR26.dbc",
            url="ftp://example/BOTUBR26.dbc",
            dataset_status="preliminary",
            size_bytes=123,
            modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ),
        months={(2026, 1), (2026, 2)},
        working_dir=tmp_path / "work",
    )

    assert [(row["Date"], row["Cases"]) for row in rows] == [
        ("2026-01-01", "2"),
        ("2026-02-01", "1"),
    ]
    assert all(row["DiseaseCode"] == "BOTU" for row in rows)


def test_aggregate_file_reloads_cache_only_when_months_covered(tmp_path, monkeypatch) -> None:
    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    item = SINANFile(
        prefix="DENG",
        disease_name="Dengue",
        year=2026,
        filename="DENGBR26.dbc",
        url="ftp://example/DENGBR26.dbc",
        dataset_status="preliminary",
        size_bytes=123,
        modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    partial_scope_path = crawler._file_cache_path(item)
    partial_scope_path.parent.mkdir(parents=True, exist_ok=True)
    partial_payload = {
        "signature": crawler._file_cache_signature(item),
        "file": item.filename,
        "prefix": item.prefix,
        "year": item.year,
        "dataset_status": item.dataset_status,
        "scope": ["2026-01"],
        "generated_at": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
        "rows": [{"Date": "2026-01-01", "Cases": 7}],
    }
    partial_scope_path.write_text(json.dumps(partial_payload), encoding="utf-8")

    fake_dbc = tmp_path / "DENGBR26.dbc"
    fake_dbc.write_bytes(b"fake")
    fake_dbf = tmp_path / "DENGBR26.dbf"
    _write_test_dbf(
        fake_dbf,
        [
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260201", "NU_ANO": "2026"},
            {"ID_AGRAVO": "A051", "DT_NOTIFIC": "20260205", "NU_ANO": "2026"},
        ],
    )

    download_calls: list[int] = []

    def _fake_download_file(_item: SINANFile, _target_dir: Path) -> Path:
        download_calls.append(1)
        return fake_dbc

    monkeypatch.setattr(crawler, "_download_file", _fake_download_file)
    monkeypatch.setattr(
        BrazilSINANCrawler,
        "_decompress_to_dbf",
        staticmethod(lambda _dbc, dbf: shutil.copyfile(fake_dbf, dbf)),
    )

    rows = crawler.aggregate_file(item, months={(2026, 2)}, working_dir=tmp_path / "work")
    assert rows == [{
        "Date": "2026-02-01",
        "Cases": "2",
        "RawDiseaseLabel": "Dengue",
        "DiseaseCode": "DENG",
        "Year": "2026",
        "Month": "2",
        "DatasetYear": "2026",
        "DatasetStatus": "preliminary",
        "SourceFile": "DENGBR26.dbc",
        "SourceURL": "ftp://example/DENGBR26.dbc",
        "Source": "Brazil DATASUS SINAN Open Data",
    }]
    assert download_calls == [1]


def test_aggregate_file_uses_full_scope_cache_without_reparse(tmp_path, monkeypatch) -> None:
    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    item = SINANFile(
        prefix="DENG",
        disease_name="Dengue",
        year=2026,
        filename="DENGBR26.dbc",
        url="ftp://example/DENGBR26.dbc",
        dataset_status="preliminary",
        size_bytes=123,
        modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    cache_path = crawler._file_cache_path(item)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    full_payload = {
        "signature": crawler._file_cache_signature(item),
        "file": item.filename,
        "prefix": item.prefix,
        "year": item.year,
        "dataset_status": item.dataset_status,
        "scope": "full",
        "generated_at": datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat(),
        "rows": [
            {"Date": "2026-01-01", "Cases": 3},
            {"Date": "2026-02-01", "Cases": 5},
            {"Date": "2026-03-01", "Cases": 6},
        ],
    }
    cache_path.write_text(json.dumps(full_payload), encoding="utf-8")

    def _should_not_download(_item: SINANFile, _target_dir: Path) -> Path:
        raise AssertionError("Unexpected download attempt on cache hit")

    monkeypatch.setattr(crawler, "_download_file", _should_not_download)
    rows = crawler.aggregate_file(item, months={(2026, 2)}, working_dir=tmp_path / "work")
    assert rows == [{
        "Date": "2026-02-01",
        "Cases": "5",
        "RawDiseaseLabel": "Dengue",
        "DiseaseCode": "DENG",
        "Year": "2026",
        "Month": "2",
        "DatasetYear": "2026",
        "DatasetStatus": "preliminary",
        "SourceFile": "DENGBR26.dbc",
        "SourceURL": "ftp://example/DENGBR26.dbc",
        "Source": "Brazil DATASUS SINAN Open Data",
    }]


def test_fetch_file_index_uses_stale_cache_when_live_listing_fails(tmp_path, monkeypatch) -> None:
    crawler = BrazilSINANCrawler(save_raw=True, raw_dir=tmp_path / "raw", cache_dir=tmp_path / "cache")
    cached_file = SINANFile(
        prefix="DENG",
        disease_name="Dengue",
        year=2026,
        filename="DENGBR26.dbc",
        url="ftp://example/DENGBR26.dbc",
        dataset_status="preliminary",
        size_bytes=123,
        modified_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    crawler._write_cached_file_index([cached_file])
    cache_path = crawler._file_index_cache_path()
    old_mtime = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(cache_path, (old_mtime, old_mtime))

    monkeypatch.setattr(
        crawler,
        "_fetch_listing_text",
        lambda _url: (_ for _ in ()).throw(TimeoutError("listing timeout")),
    )

    files = crawler.fetch_file_index()

    assert files == [cached_file]


def test_br_monthly_updater_history_months_respects_year_window(tmp_path) -> None:
    updater = BRMonthlyUpdater(output_csv=tmp_path / "brazil_national_monthly.csv")
    assert updater.history_months(start_year=2025, end_date=date(2026, 2, 1)) == [
        (2025, 1),
        (2025, 2),
        (2025, 3),
        (2025, 4),
        (2025, 5),
        (2025, 6),
        (2025, 7),
        (2025, 8),
        (2025, 9),
        (2025, 10),
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]


def test_br_refresh_source_can_skip_csv_fallback_and_live_write(tmp_path) -> None:
    output_csv = tmp_path / "brazil_national_monthly.csv"
    updater = BRMonthlyUpdater(output_csv=output_csv)

    rows = [
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "Dengue",
            "DiseaseCode": "DENG",
            "Cases": "13",
            "DatasetStatus": "final",
            "SourceFiles": "DENGBR26.dbc",
            "SourceURLs": "http://example/DENGBR26.dbc",
            "Source": "Brazil DATASUS SINAN Open Data",
        },
        {
            "Date": "2026-01-01",
            "RawDiseaseLabel": "Dengue",
            "DiseaseCode": "DENG",
            "Cases": "1",
            "DatasetStatus": "preliminary",
            "SourceFiles": "DENGBR26.dbc",
            "SourceURLs": "http://example/DENGBR26.dbc",
            "Source": "Brazil DATASUS SINAN Open Data",
        },
    ]

    class DummyCrawler:
        def crawl_monthly_national(self, *_args, **_kwargs):
            return BRFetchSummary(
                row_count=len(rows),
                latest_date=date(2026, 1, 1),
                files_fetched=1,
                source_url="http://example",
                rows=rows,
            )

    fetched = updater.refresh_source(
        source="sinan_datasus",
        months=[(2026, 1)],
        load_csv_fallback=False,
        write_csv=False,
        crawler=DummyCrawler(),
    )

    assert len(fetched.rows) == 2
    assert not output_csv.exists()


def test_br_refresh_source_recovers_with_csv_when_only_trailing_month_missing(tmp_path) -> None:
    output_csv = tmp_path / "brazil_national_monthly.csv"
    updater = BRMonthlyUpdater(output_csv=output_csv)
    updater._write_rows_to_output_csv(
        [
            {
                "Date": "2026-04-01",
                "RawDiseaseLabel": "Dengue",
                "DiseaseCode": "DENG",
                "Cases": "13",
                "DatasetStatus": "preliminary",
                "SourceFiles": "DENGBR26.dbc",
                "SourceURLs": "http://example/DENGBR26.dbc",
                "Source": "Brazil DATASUS SINAN Open Data",
            },
            {
                "Date": "2026-05-01",
                "RawDiseaseLabel": "Dengue",
                "DiseaseCode": "DENG",
                "Cases": "21",
                "DatasetStatus": "preliminary",
                "SourceFiles": "DENGBR26.dbc",
                "SourceURLs": "http://example/DENGBR26.dbc",
                "Source": "Brazil DATASUS SINAN Open Data",
            },
        ]
    )

    class FailingCrawler:
        def crawl_monthly_national(self, *_args, **_kwargs):
            raise RuntimeError("[BR-SINAN] No SINAN DBC files matched requested months/prefixes")

    fetched = updater.refresh_source(
        source="sinan_datasus",
        months=[(2026, 4), (2026, 5), (2026, 6)],
        crawler=FailingCrawler(),
    )

    assert [(row["Date"], row["Cases"]) for row in fetched.rows] == [
        ("2026-04-01", "13"),
        ("2026-05-01", "21"),
    ]
    assert fetched.source_latest_date == date(2026, 5, 1)
    assert any("previous CSV covers 2/3" in line for line in fetched.script_logs)


def test_br_write_rows_to_output_csv_keeps_sorted_output(tmp_path) -> None:
    output_csv = tmp_path / "brazil_national_monthly.csv"
    updater = BRMonthlyUpdater(output_csv=output_csv)
    updater._write_rows_to_output_csv(
        [
            {
                "Date": "2026-02-01",
                "RawDiseaseLabel": "Zika",
                "DiseaseCode": "ZIKA",
                "Cases": "4",
                "DatasetStatus": "final",
                "SourceFiles": "ZIKABR26.dbc",
                "SourceURLs": "https://example/ZIKABR26.dbc",
                "Source": "Brazil DATASUS SINAN Open Data",
            },
            {
                "Date": "2026-01-01",
                "RawDiseaseLabel": "Dengue",
                "DiseaseCode": "DENG",
                "Cases": "8",
                "DatasetStatus": "final",
                "SourceFiles": "DENGBR26.dbc",
                "SourceURLs": "https://example/DENGBR26.dbc",
                "Source": "Brazil DATASUS SINAN Open Data",
            },
        ]
    )

    text = output_csv.read_text(encoding="utf-8")
    assert "2026-01-01" in text
    assert "2026-02-01" in text
    assert text.index("2026-01-01") < text.index("2026-02-01")
