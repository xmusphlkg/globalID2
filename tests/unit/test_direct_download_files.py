from __future__ import annotations

import json

from openpyxl import load_workbook
import pytest

from src.generation.direct_download_files import (
    build_direct_download_files,
    partition_rows,
)


def _context(
    dates: list[str] | None = None,
    *,
    generated_at: str = "2026-08-01T00:00:00+00:00",
    dataset_name: str = "Hepatitis A",
) -> dict:
    dates = dates or ["2010-01-01", "2015-01-01", "2020-01-01", "2026-01-01"]
    count = len(dates)
    source_info = {
        "primary_scope": "official",
        "primary_label": "Official source",
        "primary_url": "https://example.test/source",
        "primary_type": "api",
        "sources": [
            {
                "scope": "official",
                "label": "Official source",
                "url": "https://example.test/source",
                "type": "api",
            }
        ],
    }
    series = {
        "disease_id": "D007",
        "name_en": dataset_name,
        "name_zh": "甲型肝炎",
        "category": "Viral",
        "dates": dates,
        "cases": list(range(1, count + 1)),
        "weekly_equiv_cases": [1.0] * count,
        "deaths": [0] * count,
        "incidence_rates": [0.1] * count,
        "incidence_sources": ["official"] * count,
        "mortality_rates": [0] * count,
    }
    return {
        "generated_at": generated_at,
        "countries_simple": [{"code": "CN", "name": "China"}],
        "country_sources_by_code": {"CN": source_info},
        "country_download_entries": [
            {
                "kind": "country",
                "id": "cn",
                "code": "CN",
                "name": "China",
                "record_count": count,
                "date_range": {"start": min(dates), "end": max(dates)},
                "site_json_path": "/site-data/countries/cn.json",
            }
        ],
        "disease_download_entries": [
            {
                "kind": "disease",
                "id": "d007",
                "disease_id": "D007",
                "record_count": count,
                "date_range": {"start": min(dates), "end": max(dates)},
                "site_json_path": "/site-data/diseases/d007.json",
            }
        ],
        "country_exports": [
            {
                "code": "CN",
                "country_name": "China",
                "source_info": source_info,
                "country_data": {
                    "country_code": "CN",
                    "country_name": "China",
                    "disease_series": {"D007": series},
                },
            }
        ],
        "disease_exports": [
            {
                "disease_id": "D007",
                "disease_data": {
                    "disease_id": "D007",
                    "slug": "hepatitis-a",
                    "name_en": dataset_name,
                    "name_zh": "甲型肝炎",
                    "category": "Viral",
                    "country_series": {"CN": series},
                    "source_info": [source_info],
                },
            }
        ],
    }


def test_partition_boundaries_use_zero_and_five_year_anchors() -> None:
    rows = [
        {"date": value}
        for value in (
            "2010-01-01",
            "2014-12-01",
            "2015-01-01",
            "2019-12-01",
            "2020-01-01",
            "2025-12-01",
            "2026-01-01",
            "2029-12-01",
            "2030-01-01",
        )
    ]

    parts = partition_rows(rows)
    assert [(part.key, part.label) for part, _ in parts] == [
        ("2030-2034", "2030–now"),
        ("2026-2029", "2026–2029"),
        ("2020-2025", "2020–2025"),
        ("2015-2019", "2015–2019"),
        ("2010-2014", "2010–2014"),
    ]


def test_current_bridge_window_is_2026_now_until_2030_exists() -> None:
    parts = partition_rows([{"date": "2026-01-01"}, {"date": "2029-01-01"}])
    assert parts[0][0].key == "2026-2029"
    assert parts[0][0].label == "2026–now"
    assert parts[0][0].is_current is True


def test_build_writes_three_formats_for_every_partition(tmp_path) -> None:
    base = "https://raw.githubusercontent.com/example/data/main"
    manifest = build_direct_download_files(_context(), tmp_path, download_url_base=base)

    entry = manifest["diseases"][0]
    current = entry["parts"][0]
    assert manifest["formats"] == ["csv", "json", "xlsx"]
    assert current["id"] == "2026-2029"
    for format_name in manifest["formats"]:
        meta = current["files"][format_name]
        assert meta["url"] == f"{base}/{meta['relative_path']}"
        assert (tmp_path / meta["relative_path"]).is_file()

    json_path = tmp_path / current["files"]["json"]["relative_path"]
    payload = json.loads(json_path.read_text())
    assert payload["metadata"]["record_count"] == 1
    assert "generated_at" not in payload["metadata"]
    assert "generated_at" not in payload["records"][0]

    xlsx_path = tmp_path / current["files"]["xlsx"]["relative_path"]
    workbook = load_workbook(xlsx_path, read_only=True)
    assert workbook.sheetnames == ["records", "metadata"]


def test_historical_partition_bytes_stay_stable_when_current_data_changes(tmp_path) -> None:
    base = "https://raw.githubusercontent.com/example/data/main"
    first = build_direct_download_files(_context(), tmp_path, download_url_base=base)
    historical = next(
        part for part in first["diseases"][0]["parts"] if part["id"] == "2020-2025"
    )
    before = {
        format_name: (tmp_path / historical["files"][format_name]["relative_path"]).read_bytes()
        for format_name in ("csv", "json", "xlsx")
    }

    second_context = _context(
        ["2010-01-01", "2015-01-01", "2020-01-01", "2026-01-01", "2027-01-01"],
        generated_at="2027-01-01T00:00:00+00:00",
    )
    second = build_direct_download_files(second_context, tmp_path, download_url_base=base)
    historical_after = next(
        part for part in second["diseases"][0]["parts"] if part["id"] == "2020-2025"
    )
    for format_name in ("csv", "json", "xlsx"):
        path = tmp_path / historical_after["files"][format_name]["relative_path"]
        assert path.read_bytes() == before[format_name]


def test_oversized_calendar_window_is_split_below_target(tmp_path) -> None:
    dates = [f"2026-{month:02d}-01" for month in range(1, 13)] + [
        f"2027-{month:02d}-01" for month in range(1, 13)
    ]
    manifest = build_direct_download_files(
        _context(dates, dataset_name="Hepatitis A " + "x" * 1000),
        tmp_path,
        download_url_base="https://raw.githubusercontent.com/example/data/main",
        max_file_bytes=12_000,
    )
    parts = manifest["diseases"][0]["parts"]
    assert len(parts) > 1
    assert all(
        file_meta["bytes"] < 12_000
        for part in parts
        for file_meta in part["files"].values()
    )


def test_direct_download_files_reject_non_raw_base(tmp_path) -> None:
    with pytest.raises(ValueError, match="GitHub Raw"):
        build_direct_download_files(
            _context(),
            tmp_path,
            download_url_base="/downloads",
        )
